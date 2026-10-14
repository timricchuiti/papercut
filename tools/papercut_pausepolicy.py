#!/usr/bin/env python3
"""papercut_pausepolicy.py <fcpxml> <whisper.json> <audio16k.wav> [--motion cnt10.npy]
                           [--keep 2.0] [--air 1.0] [--override t=air ...] [--dry]

Post-process a PaperCut FCPXML (which cuts every pause > ~0.4 s to ~0.14 s) to the
"screen-recording" pause policy:
  * pure pause (no transcribed words were cut inside the gap) and gap <= --keep  -> keep it whole (merge clips)
  * pure pause longer than --keep -> keep --air seconds of it; the removed window is placed
    where it swallows the most screen motion / off-mic sound, so a short on-screen action
    next to speech survives and the kept air is still and silent
  * gap that contains cut words (flub / retake) -> left as exported (tight join), unless an
    --override t=air (t = approx source time inside the gap) asks for air there
Markers travel with their clips. Re-tiles timeline offsets, bumps the sequence duration.
"""
import re,sys,json,numpy as np,soundfile as sf
args=sys.argv[1:]; opts={'keep':2.0,'air':1.0,'motion':None,'dry':False,'override':[]}
pos=[]
i=0
while i<len(args):
    a=args[i]
    if a=='--keep': opts['keep']=float(args[i+1]); i+=2
    elif a=='--air': opts['air']=float(args[i+1]); i+=2
    elif a=='--motion': opts['motion']=args[i+1]; i+=2
    elif a=='--override': t,v=args[i+1].split('='); opts['override'].append((float(t),float(v))); i+=2
    elif a=='--dry': opts['dry']=True; i+=1
    else: pos.append(a); i+=1
fx,jp,wav=pos
x=open(fx).read()
# ---- clips (whole elements, markers inside) ----
clip_re=re.compile(r'<asset-clip\b[^>]*?(?:/>|>.*?</asset-clip>)',re.S)
attr_re=re.compile(r'(offset=")(\d+)/(\d+)s("[^>]*duration=")(\d+)/\d+s("[^>]*start=")(\d+)/\d+s(")')
ms=list(clip_re.finditer(x)); assert ms
clips=[]
for m in ms:
    t=m.group(0); a=attr_re.search(t); fps=int(a[3])
    clips.append({'text':t,'off':int(a[2]),'dur':int(a[5]),'start':int(a[7]),'markers':re.findall(r'<marker\b[^>]*/>',t)})
head=x[:ms[0].start()]; tail=x[ms[-1].end():]
seps=[x[ms[k].end():ms[k+1].start()] for k in range(len(ms)-1)]
sep=max(set(seps),key=seps.count) if seps else '\n'
# ---- words ----
d=json.load(open(jp)); W=[]
for s in d['segments']:
    for w in s['words']:
        du=w['end']-w['start']
        if 0.02<=du<=1.2: W.append(((w['start']+w['end'])/2,w['word']))   # skip ghosts and pause-stretched words
# ---- loudness (0.05 s) and motion (0.1 s) ----
a,sr=sf.read(wav); hop=int(sr*0.05); n=len(a)//hop
rms=np.sqrt(np.mean(a[:n*hop].reshape(n,hop)**2,axis=1)); loud=rms>0.01
if opts['motion']:
    cnt=np.load(opts['motion']); mot=cnt>2
else: mot=np.zeros(n//2+1,bool)
def score(t0,t1):
    """cost of KEEPING source [t0,t1): loud frames weigh 1, screen-motion frames 0.5"""
    i0,i1=int(t0/0.05),int(t1/0.05); s=float(loud[i0:i1].sum())
    j0,j1=int(t0/0.1),int(t1/0.1); s+=0.5*float(mot[j0:j1].sum())
    return s
F=fps
def fmt(t): return f"{int(t//60)}:{t%60:05.2f}"
out=[clips[0]]; log=[]
for c in clips[1:]:
    p=out[-1]
    a_end=(p['start']+p['dur'])/F; b_start=c['start']/F; g=b_start-a_end
    if g>600:
        out.append(c); continue
    if abs(g)<=0.001:
        p['dur']=c['start']+c['dur']-p['start']; p['markers']+=c['markers']; continue
    words=[w for m_,w in W if a_end-0.03<m_<b_start+0.03]
    ov=[v for t,v in opts['override'] if a_end<=t<=b_start]
    if words and not ov:
        out.append(c); continue
    if ov: air=ov[0]
    elif g<=opts['keep']: air=g
    else: air=opts['air']
    if air>=g-0.05:
        # merge whole gap
        p['dur']=c['start']+c['dur']-p['start']; p['markers']+=c['markers']
        log.append(f"{fmt(a_end)} gap {g:4.2f}s kept whole (merge)"); continue
    # removal window of length g-air must cover the cut words (if any)
    wl=[m_ for m_,w in W if a_end-0.03<m_<b_start+0.03]
    lo_c0=a_end; hi_c0=a_end+air
    if wl: hi_c0=min(hi_c0,min(wl)-0.08); lo_c0=max(lo_c0,max(wl)+0.08-(g-air))
    if hi_c0<lo_c0: hi_c0=lo_c0
    best=None
    for k in range(int(round((hi_c0-lo_c0)/0.05))+1):
        c0=lo_c0+k*0.05; c1=c0+(g-air)
        sc=score(a_end,c0)+score(c1,b_start); pref=abs((c0-a_end)-air/2)
        if best is None or (sc,pref)<(best[0],best[1]): best=(sc,pref,c0,c1)
    sc,_,c0,c1=best
    p['dur']=round(c0*F)-p['start']
    nd=round((c1)*F); c['dur']=c['start']+c['dur']-nd; c['start']=nd
    out.append(c)
    log.append(f"{fmt(a_end)} gap {g:5.2f}s -> air {air:.2f}s (head {c0-a_end:.2f} / tail {b_start-c1:.2f}){' words:'+' '.join(words[:6]) if words else ''}{'  [kept motion/loud score %.1f]'%sc if sc else ''}")
# ---- re-tile ----
off=clips[0]['off']; parts=[]
for c in out:
    t=c['text']; a=attr_re.search(t)
    t=t[:a.start()]+f"{a[1]}{off}/{F}s{a[4]}{c['dur']}/{F}s{a[6]}{c['start']}/{F}s{a[8]}"+t[a.end():]
    # markers: rebuild element with this clip's marker list
    mk=c['markers']
    body=re.sub(r'<marker\b[^>]*/>\s*','',t)
    if mk:
        if body.rstrip().endswith('/>'):
            body=body.rstrip()[:-2]+'>'+''.join('\n'+m for m in mk)+'\n</asset-clip>'
        else:
            body=body.replace('</asset-clip>',''.join(m+'\n' for m in mk)+'</asset-clip>')
    parts.append(body); off+=c['dur']
x2=head+sep.join(parts)+tail
m=re.search(r'(<sequence[^>]*duration=")(\d+)(/\d+s")',x2)
total=off-clips[0]['off']
if m: x2=x2[:m.start()]+f'{m[1]}{total}{m[3]}'+x2[m.end():]
print('\n'.join(log))
print(f"clips {len(clips)} -> {len(out)}; markers {sum(len(c['markers']) for c in out)}; total {fmt(total/F)}")
if not opts['dry']: open(fx,'w').write(x2)
