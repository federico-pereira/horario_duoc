import streamlit as st
import pandas as pd
import re
from datetime import datetime, time
from itertools import product
from collections import defaultdict
import matplotlib.pyplot as plt
import matplotlib.patches as patches

st.set_page_config(layout="wide")
st.title("Generador de Horarios - Ventana Horaria Preferida")

# -------------------
# Helpers & Parsers
# -------------------
DAY_FULL = {
    "Lunes":"Lu","Martes":"Ma","Miércoles":"Mi","Miercoles":"Mi",
    "Jueves":"Ju","Viernes":"Vi","Sábado":"Sa","Sabado":"Sa","Domingo":"Do"
}
pattern_sched = (
    r"(Lunes|Martes|Mi[eé]rcoles|Jueves|Viernes|S[áa]bado|Domingo)"
    r"\s+(\d{1,2}:\d{2})\s+a\s+(\d{1,2}:\d{2})"
)

def build_sections(df):
    """Read each CSV row, parse its 'Cátedra' into meetings, and group by section."""
    by_sec = defaultdict(list)
    for _, r in df.iterrows():
        by_sec[r['SSEC']].append(r)

    secs = []
    for sec_id, rows in by_sec.items():
        meetings = []
        for r in rows:
            for day_full, t0, t1 in re.findall(pattern_sched, r['Cátedra']):
                d = DAY_FULL.get(day_full, day_full[:2])
                s = datetime.strptime(t0, "%H:%M").time()
                e = datetime.strptime(t1, "%H:%M").time()
                meetings.append((d, s, e))
        # remove duplicate slots
        meetings = list(dict.fromkeys(meetings))
        secs.append((sec_id, rows[0]['Asignatura'], meetings, rows[0]['Profesor']))
    return [Section(*args) for args in secs]

class Section:
    def __init__(self, cid, course, meetings, teacher):
        self.cid = cid
        self.course = course
        self.meetings = meetings
        self.teacher = teacher

    def __str__(self):
        times = "; ".join(f"{d} {s.strftime('%H:%M')}-{e.strftime('%H:%M')}"
                          for d, s, e in self.meetings)
        return f"[{self.cid}] {self.course} — {times} — {self.teacher}"

def overlaps(a, b):
    for d1, s1, e1 in a.meetings:
        for d2, s2, e2 in b.meetings:
            if d1 == d2 and s1 < e2 and s2 < e1:
                return True
    return False

def compute_window(combo):
    max_gap = 0
    by_day = defaultdict(list)
    for sec in combo:
        for d, s, e in sec.meetings:
            by_day[d].append((s, e))
    for meetings in by_day.values():
        meetings.sort(key=lambda x: x[0])
        for i in range(len(meetings) - 1):
            end_prev = meetings[i][1]
            start_next = meetings[i+1][0]
            gap = (start_next.hour*60 + start_next.minute) - (end_prev.hour*60 + end_prev.minute)
            max_gap = max(max_gap, gap)
    return max_gap

# -------------------
# Scheduling Logic
# -------------------
def compute_schedules(courses, ranking, min_days_free, banned,
                      start_pref, end_pref, weights):
    hard_window = weights['window'] == 5
    hard_veto   = weights['veto']   == 5

    combos = list(product(*courses.values()))
    metrics = []
    for combo in combos:
        # 1) No overlapping classes
        if any(overlaps(a, b) for a in combo for b in combo if a != b):
            continue

        # 2) Minimum free days
        days_occ = {d for sec in combo for d, _, _ in sec.meetings}
        if (5 - len(days_occ)) < min_days_free:
            continue

        # 3) Vetoed teachers
        veto_cnt = sum(sec.teacher in banned for sec in combo)
        if hard_veto and veto_cnt > 0:
            continue

        # 4) Time‐window violations
        win_vio = 0
        for sec in combo:
            for _, s, e in sec.meetings:
                if s < start_pref or e > end_pref:
                    win_vio += 1
        if hard_window and win_vio > 0:
            continue

        # Metrics for scoring
        avg_rank = sum(ranking.get(sec.teacher, len(ranking)) for sec in combo) / len(combo)
        win_gap  = compute_window(combo)
        free_days = 5 - len(days_occ)

        metrics.append((combo, avg_rank, win_gap, free_days, veto_cnt, win_vio))

    if not metrics:
        return []

    # Normalize each metric
    max_vals = {i: max(vals) for i, vals in enumerate(zip(*[m[1:] for m in metrics]))}
    total_w  = sum(weights.values())
    scored   = []
    for combo, avg, gap, free, veto, vio in metrics:
        n1 = 1 - (avg / max_vals[0])
        n2 = 1 - (gap / max_vals[1])
        n3 =  free / max_vals[2]
        n4 = 1 - (veto / max_vals[3])
        n5 = 1 - (vio  / max_vals[4])
        score = (
            weights['rank']   * n1 +
            weights['win']    * n2 +
            weights['off']    * n3 +
            weights['veto']   * n4 +
            weights['window'] * n5
        ) / total_w
        scored.append((score, combo))

    return sorted(scored, key=lambda x: x[0], reverse=True)

# -------------------
# Visualization
# -------------------
def visualize_schedule(combo):
    DAY_MAP = {'Lu':0,'Ma':1,'Mi':2,'Ju':3,'Vi':4}
    labels  = ["Lunes","Martes","Mié","Jue","Vie"]
    fig, ax = plt.subplots(figsize=(10,6))
    ax.set_xticks([i+0.5 for i in range(5)])
    ax.set_xticklabels(labels)
    ax.set_ylim(20, 8)
    ax.set_xlim(0, 5)
    ax.set_ylabel("Hora")
    ax.grid(True, which='both', linestyle='--', linewidth=0.5)

    colors, cmap = plt.cm.tab20.colors, {}
    for sec in combo:
        if sec.course not in cmap:
            cmap[sec.course] = colors[len(cmap) % len(colors)]
        c = cmap[sec.course]
        for d, s, e in sec.meetings:
            x = DAY_MAP.get(d)
            if x is None:
                continue
            y0 = s.hour + s.minute/60
            h  = (e.hour + e.minute/60) - y0
            rect = patches.Rectangle((x+0.05, y0), 0.9, h,
                                     facecolor=c, edgecolor='black', alpha=0.6)
            ax.add_patch(rect)
            ax.text(x+0.5, y0 + h/2, f"{sec.cid}\n{sec.course}",
                    ha='center', va='center', fontsize=7)

    st.pyplot(fig)

# -------------------
# Main App
# -------------------
def main():
    # 1) Try remote CSV, fall back to uploader
    CSV_URL = "https://raw.githubusercontent.com/federico-pereira/horario_25-2/main/horario.csv"
    try:
        df = pd.read_csv(CSV_URL)
        st.success("✅ Cargado CSV desde GitHub")
    except Exception as e:
        st.warning(f"No pude cargar el CSV remoto: {e}")
        uploaded = st.file_uploader("Sube tu CSV", type="csv")
        if not uploaded:
            st.stop()
        df = pd.read_csv(uploaded)

    # 2) Build sections & group by course
    secs    = build_sections(df)
    courses = defaultdict(list)
    for sec in secs:
        courses[sec.course].append(sec)

    # Sidebar controls
    st.sidebar.header("Asignaturas")
    chosen = st.sidebar.multiselect("Selecciona asignaturas:", sorted(courses), sorted(courses))
    sub    = {c: courses[c] for c in chosen}

    st.sidebar.header("Ranking Docentes")
    teachers = sorted({sec.teacher for secs in sub.values() for sec in secs})
    rank_sel = st.sidebar.multiselect("Orden (mejor primero):", teachers, teachers)
    ranking  = {t:i for i,t in enumerate(rank_sel)}

    st.sidebar.header("Días Libres Mínimos")
    min_free = st.sidebar.slider("Días libres (0–5):", 0, 5, 0)

    st.sidebar.header("Docentes Vetados")
    banned   = st.sidebar.multiselect("Veto:", teachers)

    st.sidebar.header("Ventana Horaria Preferida")
    start_pref = st.sidebar.time_input("Inicio preferido:", time(8,30))
    end_pref   = st.sidebar.time_input("Fin preferido:",    time(18,0))

    st.sidebar.header("Pesos de Criterio")
    weights = {
        'rank':   st.sidebar.slider("Ranking",         1.0, 5.0, 3.0),
        'win':    st.sidebar.slider("Ventana pausa",   1.0, 5.0, 3.0),
        'off':    st.sidebar.slider("Días libres",     1.0, 5.0, 3.0),
        'veto':   st.sidebar.slider("Veto",            1.0, 5.0, 3.0),
        'window': st.sidebar.slider("Ventana horaria", 1.0, 5.0, 3.0),
    }

    # 3) Generate schedules
    if st.sidebar.button("Generar Horarios"):
        scored = compute_schedules(
            sub, ranking, min_free, banned,
            start_pref, end_pref, weights
        )
        if not scored:
            st.warning("No hay soluciones válidas.")
        else:
            st.header("Top 5 Horarios")
            for score, combo in scored[:5]:
                st.subheader(f"Score: {score:.3f}")
                for sec in combo:
                    st.write(sec)
                st.markdown("---")
            st.header("Mejor Horario")
            visualize_schedule(scored[0][1])

if __name__ == "__main__":
    main()
