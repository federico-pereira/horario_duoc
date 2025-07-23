import streamlit as st
import pandas as pd
import re
from datetime import datetime, time
from itertools import product
from collections import defaultdict
import matplotlib.pyplot as plt
import matplotlib.patches as patches

st.set_page_config(layout="wide")
st.title("Generador de Horarios – Versión Robusta")

# --- Regex para extraer día y horas ---
slot_re = re.compile(
    r"(Lunes|Martes|Mi[eé]rcoles|Jueves|Viernes|S[áa]bado|Domingo)?\s*"
    r"(\d{1,2}:\d{2}(?::\d{2})?)\s*-\s*(\d{1,2}:\d{2}(?::\d{2})?)"
)
DAY_SHORT = {
    "Lunes":"Lu","Martes":"Ma","Miércoles":"Mi","Miercoles":"Mi",
    "Jueves":"Ju","Viernes":"Vi","Sábado":"Sa","Sabado":"Sa","Domingo":"Do"
}

class Section:
    def __init__(self, cid, course, meetings, teacher):
        self.cid      = cid
        self.course   = course
        self.meetings = meetings
        self.teacher  = teacher
    def __str__(self):
        times = "; ".join(f"{d} {s.strftime('%H:%M')}-{e.strftime('%H:%M')}"
                          for d,s,e in self.meetings)
        return f"[{self.cid}] {self.course} — {times} — {self.teacher}"

@st.cache_data
def build_courses(df: pd.DataFrame, id_col: str):
    groups = defaultdict(list)
    for _,r in df.iterrows():
        sec = r[id_col]
        groups[sec].append(r)
    courses = defaultdict(list)
    for sec_id,rows in groups.items():
        meetings=[]
        for r in rows:
            raw=r["Horario"]
            for day,t0,t1 in slot_re.findall(raw):
                d=DAY_SHORT.get(day)
                if not d: continue
                fmt0="%H:%M:%S" if t0.count(":")==2 else "%H:%M"
                fmt1="%H:%M:%S" if t1.count(":")==2 else "%H:%M"
                try:
                    s=datetime.strptime(t0,fmt0).time()
                    e=datetime.strptime(t1,fmt1).time()
                    meetings.append((d,s,e))
                except:
                    continue
        meetings=list(dict.fromkeys(meetings))
        course = rows[0]["Asignatura"]
        teacher= rows[0]["Docente"]
        courses[course].append(Section(sec_id,course,meetings,teacher))
    return courses

def overlaps(a,b):
    for d1,s1,e1 in a.meetings:
        for d2,s2,e2 in b.meetings:
            if d1==d2 and s1<e2 and s2<e1:
                return True
    return False

def compute_window(combo):
    max_gap=0
    days=defaultdict(list)
    for sec in combo:
        for d,s,e in sec.meetings:
            days[d].append((s,e))
    for mts in days.values():
        mts.sort(key=lambda x:x[0])
        for i in range(len(mts)-1):
            gap=(mts[i+1][0].hour*60+mts[i+1][0].minute) - (mts[i][1].hour*60+mts[i][1].minute)
            max_gap=max(max_gap,gap)
    return max_gap

def compute_schedules(courses, ranking, min_free, banned,
                      start_pref, end_pref, weights):
    hard_slot = weights['slot']==5
    hard_veto = weights['veto']==5
    combos = list(product(*courses.values()))
    metrics=[]
    for combo in combos:
        if any(overlaps(a,b) for a in combo for b in combo if a!=b): continue
        days_occ={d for sec in combo for d,_,_ in sec.meetings}
        if 5-len(days_occ)<min_free: continue
        veto_cnt=sum(sec.teacher in banned for sec in combo)
        if hard_veto and veto_cnt>0: continue
        slot_vio=sum(
            1 for sec in combo for _,s,e in sec.meetings
            if s<start_pref or e> end_pref
        )
        if hard_slot and slot_vio>0: continue
        avg_rank=sum(ranking.get(sec.teacher,len(ranking)) for sec in combo)/len(combo)
        win_gap=compute_window(combo)
        free_days=5-len(days_occ)
        metrics.append((combo,avg_rank,win_gap,free_days,veto_cnt,slot_vio))
    if not metrics: return []
    maxs=[max(vals) or 1 for vals in zip(*[m[1:] for m in metrics])]
    total_w=sum(weights.values())
    scored=[]
    for combo,avg,win,off,veto,slot_v in metrics:
        n1=1-avg/maxs[0]; n2=1-win/maxs[1]
        n3=off/maxs[2];   n4=1-veto/maxs[3]
        n5=1-slot_v/maxs[4]
        sc=(weights['rank']*n1+weights['win']*n2+
            weights['off']*n3 +weights['veto']*n4+
            weights['slot']*n5)/total_w
        scored.append((sc,combo))
    return sorted(scored,key=lambda x:x[0],reverse=True)

def visualize(combo):
    idx={'Lu':0,'Ma':1,'Mi':2,'Ju':3,'Vi':4}
    labels=["Lunes","Martes","Mié","Jue","Vie"]
    fig,ax=plt.subplots(figsize=(10,6))
    ax.set_xticks([i+0.5 for i in range(5)])
    ax.set_xticklabels(labels)
    ax.set_xlim(0,5); ax.set_ylim(20,8)
    ax.grid(True,which='both',linestyle='--',linewidth=0.5)
    cmap={}; colors=plt.cm.tab20.colors
    for sec in combo:
        for d,s,e in sec.meetings:
            if d not in idx: continue
            y0=s.hour+s.minute/60; h=(e.hour+e.minute/60)-y0
            if h<=0: continue
            x=idx[d]
            c=cmap.setdefault(sec.course,colors[len(cmap)%len(colors)])
            ax.add_patch(patches.Rectangle((x+0.05,y0),0.9,h,facecolor=c,edgecolor='k',alpha=0.6))
            ax.text(x+0.5,y0+h/2,sec.cid,ha='center',va='center',fontsize=7)
    st.pyplot(fig)

# — Carga CSV desde GitHub o upload —
csv_url = st.sidebar.text_input("URL raw GitHub CSV:",
    "https://raw.githubusercontent.com/federico-pereira/horario_25-2/main/horario.csv")
try:
    df=pd.read_csv(csv_url)
    st.sidebar.success("✅ CSV remoto cargado")
except:
    up=st.sidebar.file_uploader("…o sube CSV local",type="csv")
    if not up: st.stop()
    df=pd.read_csv(up)

# — Detección dinámica de columna Sección —
candidates=[c for c in df.columns if c.lower() in ("sección","seccion","ssec")]
if not candidates:
    st.error("No encontré columna de sección en tu CSV.")
    st.stop()
id_col=candidates[0]

# — Filtros básicos —
for col in ["Sede","Carrera","Plan","Jornada","Nivel"]:
    if col in df.columns:
        val=st.sidebar.selectbox(col,sorted(df[col].dropna().unique()))
        df=df[df[col]==val]

# — Construir cursos y UI selections —
courses=build_courses(df,id_col)
all_courses=sorted(courses)
chosen=st.sidebar.multiselect("Asignaturas:",all_courses,default=all_courses[:3])
if not chosen: st.stop()
sub={c:courses[c] for c in chosen}

teachers=sorted({sec.teacher for secs in sub.values() for sec in secs})
rank_sel=st.sidebar.multiselect("Ranking (mejor→peor):",teachers,default=teachers)
ranking ={t:i for i,t in enumerate(rank_sel)}
banned  =st.sidebar.multiselect("Docentes vetados:",teachers)

start_pref=st.sidebar.time_input("Desde:",time(8,30))
end_pref  =st.sidebar.time_input("Hasta:",time(16,0))
min_free  =st.sidebar.slider("Días libres mínimos:",0,5,0)
weights   ={
    'rank':st.sidebar.slider("Peso ranking",1,5,3),
    'win' :st.sidebar.slider("Peso ventana",1,5,3),
    'off' :st.sidebar.slider("Peso días libres",1,5,3),
    'veto':st.sidebar.slider("Peso vetos",1,5,3),
    'slot':st.sidebar.slider("Peso franja",1,5,3),
}

if "scored" not in st.session_state:
    st.session_state.scored=None

if st.sidebar.button("Generar horarios"):
    st.session_state.scored=compute_schedules(
        sub,ranking,min_free,banned,
        start_pref,end_pref,weights
    )

if st.session_state.scored:
    top5=st.session_state.scored[:5]
    choice=st.sidebar.radio("Elige solución:",
        [f"Sol {i+1} ({s[0]:.2f})" for i,s in enumerate(top5)])
    idx=int(choice.split()[1].strip("()"))-1
    st.subheader(f"Solución {idx+1} (score {top5[idx][0]:.2f})")
    for sec in top5[idx][1]:
        st.write(sec)
    st.write("### Gráfico")
    visualize(top5[idx][1])
elif st.session_state.scored is None:
    st.info("Pulsa «Generar horarios» para iniciar.")
else:
    st.warning("No hay soluciones válidas.")
