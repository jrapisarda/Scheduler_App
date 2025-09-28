import pandas as pd
from datetime import datetime, timedelta, time
from collections import defaultdict
from caas_jupyter_tools import display_dataframe_to_user

# ----------------- Parameters -----------------
start_date = datetime(2025, 10, 5)  # Sunday
weeks = 4
days_total = weeks * 7

# People & constraints
LEAD = "Patty Golden"
PEOPLE = [
    LEAD,
    "Nicole Dempster",
    "Vicki Theler",
    "Mayra Bradley",
    "Lisa Dixon",
    "Shala Johnson",
    "Chloe Gray",
    "Tash Jaramillo",
    "NewHire A",
    "NewHire B",
    "NewHire C",
]

# Base weekly targets (pre-OT). Patty has 60 (5x8 + early+late per weekday), Nicole 30 (nights only), Vicki 20 (hard legal)
BASE_TARGET = {p: 40 for p in PEOPLE}
BASE_TARGET[LEAD] = 60
BASE_TARGET["Nicole Dempster"] = 30
BASE_TARGET["Vicki Theler"] = 20

# Hard availability/caps
NIGHTS_ONLY = {"Nicole Dempster"}
CANNOT_WORK_DOW = {"Mayra Bradley": {"Fri"}}
DAYS_ONLY = {LEAD}
MIN_REST_HOURS = 10
MAX_CONSECUTIVE_DAYS = 5

# ----------------- Shifts & slots -----------------
def hours_between(ts, te):
    d0 = datetime(2025,1,1)
    s = datetime.combine(d0, ts); e = datetime.combine(d0, te)
    if e <= s: e += timedelta(days=1)
    return (e - s).total_seconds()/3600.0

# Shift definitions
DAY12 = ("DAY12", time(7,0), time(19,0))     # 12h
PATTY8 = ("PATTY8", time(8,0), time(16,0))   # 8h Patty fixed
EARLY1 = ("EARLY1", time(7,0), time(8,0))    # 1h to lift 07:00 to 4 headcount
LATE3  = ("LATE3",  time(16,0), time(19,0))  # 3h to keep 16-19 at 4

N12    = ("N12", time(19,0), time(7,0))      # 12h
N105_A = ("N105A", time(19,0), time(5,30))   # 10.5h
N105_B = ("N105B", time(21,30), time(8,0))   # 10.5h

def day_slots(date):
    wkday = date.weekday()  # Mon=0 ... Sun=6
    slots = []
    if wkday < 5:
        # 3x12, Patty 8, early1, late3  => 48 day hours
        for i in range(3):
            slots.append(("Day", f"D{i+1}", DAY12[1], DAY12[2]))
        slots.append(("Day", "PATTY", PATTY8[1], PATTY8[2]))
        slots.append(("Day", "EARLY1", EARLY1[1], EARLY1[2]))
        slots.append(("Day", "LATE3",  LATE3[1],  LATE3[2]))
    else:
        # Weekends: 4x12 => 48 day hours
        for i in range(4):
            slots.append(("Day", f"D{i+1}", DAY12[1], DAY12[2]))
    return slots

def night_slots(date):
    # Aim: 3 on nights; mix of 12h and 10.5h to help Nicole hit 30
    # We'll use [N105A, N105B, N12] pattern
    return [("Night", "N1", N105_A[1], N105_A[2]),
            ("Night", "N2", N105_B[1], N105_B[2]),
            ("Night", "N3", N12[1],    N12[2])]

def slots_for_day(date):
    return night_slots(date) + day_slots(date)

# ----------------- Feasibility & selection -----------------
def week_start(d):
    # Sunday as week start
    if d.weekday() == 6: return d
    return d - timedelta(days=d.weekday()+1)

def can_work(person, date, period, start_t, end_t, last_end_dt, worked_days_count):
    # Window rules
    if person in NIGHTS_ONLY and period != "Night":
        return False
    if person in DAYS_ONLY and period != "Day":
        return False
    if person in CANNOT_WORK_DOW and date.strftime("%a") in CANNOT_WORK_DOW[person]:
        return False
    # Rest rule
    le = last_end_dt.get(person)
    start_dt = datetime.combine(date, start_t)
    end_dt = datetime.combine(date, end_t)
    if end_t <= start_t: end_dt += timedelta(days=1)
    if le is not None and (start_dt - le) < timedelta(hours=MIN_REST_HOURS):
        return False
    # Consecutive days rule
    # If this shift is day-part (07-19) or night-part (19-07), count a day worked for the date
    # Prevent >5 consecutive
    wd = worked_days_count.get(person, [])
    # Count consecutive streak: if last day in wd is yesterday's date, streak+1 else reset
    if wd:
        last_day = wd[-1]
        if (date - last_day).days == 1:
            if len(wd) >= MAX_CONSECUTIVE_DAYS:
                return False
        elif (date - last_day).days < 0:
            # shouldn't happen in forward build
            pass
    return True

def add_worked_day(person, date, worked_days_count):
    seq = worked_days_count.setdefault(person, [])
    if not seq or seq[-1] != date:
        # If previous day worked is not yesterday, reset
        if seq and (date - seq[-1]).days != 1:
            worked_days_count[person] = [date]
        else:
            seq.append(date)

# ----------------- Build schedule -----------------
assignments = []
weekly_hours = defaultdict(float)
weekly_hours_by_week = defaultdict(lambda: defaultdict(float))
last_end_dt = {}
worked_days_seq = {}

def assign(person, date, period, role, start_t, end_t):
    hrs = hours_between(start_t, end_t)
    wk = week_start(date)
    assignments.append({
        "date": date, "period": period, "role": role, "person": person,
        "start": start_t, "end": end_t, "hours": hrs, "week_start": wk
    })
    weekly_hours[person] += hrs
    weekly_hours_by_week[wk][person] += hrs
    # set last end
    end_dt = datetime.combine(date, end_t)
    if end_t <= start_t: end_dt += timedelta(days=1)
    last_end_dt[person] = end_dt
    # track day worked (for consecutive count)
    add_worked_day(person, date, worked_days_seq)

def choose_person(date, period, start_t, end_t):
    hrs = hours_between(start_t, end_t)
    wk = week_start(date)
    candidates = []
    for p in PEOPLE:
        # Patty only for PATTY slot; but if period Day and role isn't PATTY, allow Patty to pick EARLY/LATE to maximize OT while keeping rest?
        # We'll explicitly assign Patty to PATTY+EARLY+LATE later. Here, skip Patty for general slots.
        if p == LEAD:
            continue
        if not can_work(p, date, period, start_t, end_t, last_end_dt, worked_days_seq):
            continue
        wh = weekly_hours_by_week[wk][p]
        base = BASE_TARGET[p]
        ot = max(0.0, wh - base)
        # Priority: people under base first; then minimal overtime; then minimal total hours to distribute evenly
        candidates.append((wh < base, ot, wh, p))
    # Sort: under-base (True first), then least overtime, then least hours
    candidates.sort(key=lambda t: (not t[0], t[1], t[2]))
    return [p for _,__,___,p in candidates]

# Build schedule day by day
for d in range(days_total):
    date = start_date + timedelta(days=d)
    slots = slots_for_day(date)

    # 1) Nights first (Nicole nights-only 30/wk bias)
    for (period, role, s, e) in [s for s in slots if s[0]=="Night"]:
        picks = choose_person(date, period, s, e)
        # Bias Nicole if she is eligible and under 30
        if "Nicole Dempster" in picks:
            idx = picks.index("Nicole Dempster")
            if weekly_hours_by_week[week_start(date)]["Nicole Dempster"] < BASE_TARGET["Nicole Dempster"] - 0.1:
                picks.insert(0, picks.pop(idx))
        person = picks[0] if picks else "UNFILLED"
        assign(person, date, period, role, s, e)

    # 2) Day Patty 8h
    if date.weekday() < 5:
        # Ensure Patty rest; can_work checked inside assign indirectly by not using for Patty; we enforce baseline
        # Check rest before assignment
        if can_work(LEAD, date, "Day", PATTY8[1], PATTY8[2], last_end_dt, worked_days_seq):
            assign(LEAD, date, "Day", "PATTY", PATTY8[1], PATTY8[2])
        # Early1 and Late3 for Patty to maximize OT
        if can_work(LEAD, date, "Day", EARLY1[1], EARLY1[2], last_end_dt, worked_days_seq):
            assign(LEAD, date, "Day", "EARLY1", EARLY1[1], EARLY1[2])
        if can_work(LEAD, date, "Day", LATE3[1], LATE3[2], last_end_dt, worked_days_seq):
            assign(LEAD, date, "Day", "LATE3", LATE3[1], LATE3[2])

    # 3) Remaining day slots
    for (period, role, s, e) in [s for s in slots if s[0]=="Day"]:
        if role in {"PATTY","EARLY1","LATE3"} and date.weekday()<5:
            # already attempted Patty; if Patty couldn't take, still need to fill with others
            if any(a for a in assignments if a["date"]==date and a["period"]=="Day" and a["role"]==role):
                continue
        picks = choose_person(date, period, s, e)
        person = picks[0] if picks else "UNFILLED"
        assign(person, date, period, role, s, e)

# ----------------- Summaries & coverage audit -----------------
df = pd.DataFrame(assignments)
df["date"] = pd.to_datetime(df["date"]).dt.date
weekly = df.groupby(["week_start","person"])["hours"].sum().unstack(fill_value=0).sort_index()

# Coverage audit at 30-min granularity
def sample_coverage(df):
    viol = []
    for d in range(days_total):
        day = (start_date + timedelta(days=d)).date()
        for k in range(48):
            t0 = (datetime.combine(datetime(2025,10,5), time(0,0)) + timedelta(minutes=30*k)).time()
            in_day = (time(7,0) <= t0 < time(19,0))
            need = 4 if in_day else 3
            have = 0
            # count active assignments excluding UNFILLED
            for _, r in df[df["date"]==day].iterrows():
                s, e = r["start"], r["end"]
                rs = datetime.combine(datetime(2025,1,1), s)
                re = datetime.combine(datetime(2025,1,1), e)
                q0 = datetime.combine(datetime(2025,1,1), t0)
                q1 = q0 + timedelta(minutes=30)
                if re <= rs: re += timedelta(days=1)
                if q1 <= q0: q1 += timedelta(days=1)
                if max(rs,q0) < min(re,q1) and r["person"] != "UNFILLED":
                    have += 1
            if have < need:
                viol.append({"date": str(day), "time": t0.strftime("%H:%M"), "needed": need, "have": have})
    return pd.DataFrame(viol)

viol = sample_coverage(df)

# Totals per week for check
weekly_totals = weekly.sum(axis=1).to_frame(name="Total Hours per Week")

# Save to Excel
excel_path = "/mnt/data/four_week_schedule_v3.xlsx"
with pd.ExcelWriter(excel_path, engine="xlsxwriter") as writer:
    df.sort_values(["date","period","start","role","person"]).to_excel(writer, sheet_name="Assignments", index=False)
    weekly.to_excel(writer, sheet_name="Weekly_Hours_By_Person")
    weekly_totals.to_excel(writer, sheet_name="Weekly_Totals")
    if not viol.empty:
        viol.to_excel(writer, sheet_name="Coverage_Violations", index=False)

display_dataframe_to_user("Weekly Hours By Person (v3)", weekly.reset_index())

excel_path
