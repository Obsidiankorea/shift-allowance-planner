"""Pure calculation model. All hours are simulation assumptions, not payment authorization."""
from __future__ import annotations
import calendar, copy, datetime as dt, json, math, os, tempfile
from pathlib import Path
import holidays
from pay_tables import SALARY, SALARY_YEAR

CYCLE=['주','야','비','비']
WORK={'주':9.5,'야':15.5,'당':24.5,'주반':4.5,'야반':7.5,'당주반':12,'당야반':12,'비':0,'야비':0}
LEAVE=['주연','야연','당연1','당연2','주특','야특','당특1','당특2','주병','야병','당병1','당병2','당병','주공','야공','당공1','당공2']
CODES=list(WORK)+LEAVE+['교육','출장','복합']
LEAVE_DAYS={'주연':1,'야연':1,'당연1':1,'당연2':2}  # 연가 잔여일 차감 가정; 수당 산입시간과 별개
LEAVE_COMP={'rate':0.86,'divisor':30}  # 연가보상비 1일 = 월봉급액 x 86% x 1/30
RATES={'소방령':[16960,5653,136331],'소방경':[15082,5027,121237],'소방위':[13779,4593,110763],'일반5':[16053,5351,129043],'일반6':[13692,4564,110065],'일반7':[12368,4123,99422],'일반8':[12113,4038,97373],'일반9':[10949,3650,88017]}

def empty_project():
    return {'version':1,'people':[],'teams':{},'rates':copy.deepcopy(RATES),'rate_year':2026,'baseline':{},'overrides':{},'adjustments':{},'holidays_add':{},'holidays_remove':[], 'work_hours':copy.deepcopy(WORK),'leave_hours':{'일반직 야연':15,'소방직 야연':8},'leave_days':copy.deepcopy(LEAVE_DAYS),'leave_comp':copy.deepcopy(LEAVE_COMP),'round_hours':True,'source':'','warnings':[],'reference':{}}

def dates(year,month):
    return [dt.date(year,month,d) for d in range(1,calendar.monthrange(year,month)[1]+1)]

def holiday_map(p,year):
    result={str(d):n for d,n in holidays.KR(years=year,language='ko').items()}
    result.update({d:n for d,n in p['holidays_add'].items() if d.startswith(str(year))})
    for d in p['holidays_remove']: result.pop(d,None)
    return result

def event(p,person,date,scenario=True,roster=True):
    """Layers: user edits (scenario) > imported Excel roster (roster) > 주야비비 cycle."""
    key=str(date); pid=person['id']
    if scenario and key in p['overrides'].get(pid,{}): return copy.deepcopy(p['overrides'][pid][key])
    if roster and key in p['baseline'].get(pid,{}): return copy.deepcopy(p['baseline'][pid][key])
    team=p['teams'][person['team']]
    code=CYCLE[((date-dt.date.fromisoformat(team['anchor'])).days+team['phase'])%4]
    return {'code':code}

def leave_credit(code,grade):
    if code not in LEAVE:return 0
    if code=='야연':return 8 if grade.startswith('소방') else 15
    if code in ['야병','야공']:return 15
    if code.endswith('2') or code=='당병':return 16
    return 8

def code_credit(p,person,c):
    credit=p.get('work_hours',WORK).get(c,leave_credit(c,person['grade']))
    if c=='야연':credit=p.get('leave_hours',{}).get('소방직 야연' if person['grade'].startswith('소방') else '일반직 야연',credit)
    return credit

def day_parts(p,person,e,d,hs):
    """One date's contribution: (credited hours incl. added/deducted time, 비번활동 hours, night hours, holiday days)."""
    c=e['code'];credit=code_credit(p,person,c)
    if c in ['교육','출장']:credit=float(e.get('hours',8))
    credit=float(e.get('credit',credit))
    credit=credit+float(e.get('extra',0))+float(e.get('education',0))+float(e.get('travel',0))-float(e.get('deduction',0))
    night=float(e.get('night',8 if c in ['야','당','야비'] else 0))
    hd=float(e.get('holiday',1 if c in ['주','당'] and (d.weekday()>=5 or str(d) in hs) else 0))
    return credit,float(e.get('activity',0)),night,hd

def calculate(p,pid,year,month,scenario=True,extra=0,roster=True):
    person=next(x for x in p['people'] if x['id']==pid)
    hs=holiday_map(p,year); raw=0.; night=0.; hd=0.; unknown=[]; acts=0.
    for d in dates(year,month):
        e=event(p,person,d,scenario,roster); c=e['code']; key=str(d)
        if c not in CODES:unknown.append(f'{key}: {c}')
        credit,act,nt,h=day_parts(p,person,e,d,hs);raw+=credit;acts+=act;night+=nt;hd+=h
    adj=p['adjustments'].get(pid,{}).get(f'{year}-{month:02}',{})
    raw+=(math.floor(acts) if p['round_hours'] else acts)+float(adj.get('hours',0))+extra
    night=max(0,night+float(adj.get('night',0)))
    total=math.floor(raw) if p['round_hours'] else raw
    base=sum(d.weekday()<5 and str(d) not in hs for d in dates(year,month))
    signed=total-base*8-hd*8+float(adj.get('basic',0))
    if p['round_hours']:signed=math.trunc(signed)
    rates=p['rates'].get(person['grade'])
    if rates is None: unknown.append('직급 단가 없음'); rates=[0,0,0]
    money=lambda x:math.trunc(x/10)*10
    ot=money(max(0,signed)*rates[0]); np=money(night*rates[1]); hp=money(hd*rates[2])
    return dict(raw=raw,total=total,base_days=base,signed=signed,night=night,holiday=hd,overtime=ot,night_pay=np,holiday_pay=hp,pay=ot+np+hp,basic=float(adj.get('basic',0)),rates=list(rates),unknown=unknown)

def salary_table(p=None):
    """(year, {grade: [1호봉, 2호봉, ...]}): the project's own table from 설정 → 봉급표, else the built-in pay_tables.py."""
    own=(p or {}).get('salary_table')
    return (int(own['year']),own['table']) if own else (SALARY_YEAR,SALARY)

def salary_outdated(p,today):
    """True once the calendar year passes the salary table year (the table must be replaced every year)."""
    return today.year>salary_table(p)[0]

def salary_of(person,p=None):
    """Monthly base pay: salary table by grade/step when a step is entered, else the manual amount."""
    year,tables=salary_table(p);table=tables.get(person.get('grade'));step=int(person.get('step') or 0)
    if table and 1<=step<=len(table):return float(table[step-1]),f'{year} 봉급표 {person["grade"]} {step}호봉'
    if person.get('salary'):return float(person['salary']),'직접 입력 본봉'
    return 0.,'본봉 미설정'

def leave_comp_daily(p,person):
    """One unused leave day's 연가보상비 (planning value, not rounded). 0 when the person's unused leave is not compensable."""
    if not person.get('leave_comp',True):return 0.
    c=p.get('leave_comp',LEAVE_COMP);return salary_of(person,p)[0]*float(c['rate'])/float(c['divisor'])

def leave_days(p,code):return float(p.get('leave_days',LEAVE_DAYS).get(code,0))

def with_code(p,pid,date,code):
    """Copy of the project with only that date's code replaced; additive education/travel/extra fields are kept."""
    q=copy.deepcopy(p);person=next(x for x in q['people'] if x['id']==pid);old=event(q,person,date)
    e={k:v for k,v in old.items() if k in ['education','travel','extra','activity','deduction','memo']};e['code']=code
    q['overrides'].setdefault(pid,{})[str(date)]=e;return q

def leave_compare(p,pid,year,month,days):
    """Pay with the given annual-leave dates vs. the same dates left empty (leave unused), net of forgone 연가보상비."""
    q=p
    for d in days:q=with_code(q,pid,d,'비')
    r=diff_value(p,pid,year,month,q,next(x for x in q['people'] if x['id']==pid),True,True)
    rate=r['after']['rates'][0]
    return dict(r,dates=list(days),days=r['leave_days'],hours=r['after']['raw']-r['before']['raw'],signed=r['after']['signed'],without=r['before']['signed'],need_hours=r['comp']/rate if rate else math.inf)

def leave_value(p,pid,date):
    """Opportunity cost of one calendar day that uses annual leave (연가); None for work days and for 특가/공가/병가."""
    person=next(x for x in p['people'] if x['id']==pid);code=event(p,person,date)['code']
    if leave_days(p,code)<=0:return None
    return dict(leave_compare(p,pid,date.year,date.month,[date]),code=code)

def has_roster(p,pid,year,month):return any(d.startswith(f'{year}-{month:02}') for d in p['baseline'].get(pid,{}))

def diff_value(p,pid,year,month,bp,bperson,scenario,roster):
    """Current plan vs. a base plan (project bp, person bperson, layers scenario/roster).
    Pay difference minus 연가보상비 of extra annual-leave days; 'base' keeps the context for the 계산 근거 popup."""
    person=next(x for x in p['people'] if x['id']==pid);a=calculate(p,pid,year,month);b=calculate(bp,bperson['id'],year,month,scenario,roster=roster)
    changed=[];before_days=after_days=0.
    for d in dates(year,month):
        old,new=event(bp,bperson,d,scenario,roster),event(p,person,d);before_days+=leave_days(p,old['code']);after_days+=leave_days(p,new['code'])
        if new!=old:changed.append((d,old['code'],new['code']))
    used=after_days-before_days;daily=leave_comp_daily(p,person);comp=daily*used
    return dict(changed=changed,before=b,after=a,gain=a['pay']-b['pay'],leave_days=used,leave_before=before_days,leave_after=after_days,daily=daily,comp=comp,net=a['pay']-b['pay']-comp,base=(bp,bperson,scenario,roster))

def change_value(p,pid,year,month,base='roster'):
    """Current plan vs. 'roster' (current 근무표: Excel month, else cycle) or 'cycle' (pure 주야비비 with no leave or swaps)."""
    person=next(x for x in p['people'] if x['id']==pid)
    return diff_value(p,pid,year,month,p,person,False,base=='roster')

def find_person(ref,person):
    """Same person in another saved project: by id, then name+grade, then name."""
    people=ref.get('people',[])
    return next((x for x in people if x['id']==person['id']),None) or next((x for x in people if (x['name'],x['grade'])==(person['name'],person['grade'])),None) or next((x for x in people if x['name']==person['name']),None)

def file_change_value(p,pid,year,month,ref):
    """Current plan vs. a saved scenario file's plan for the same person (its own settings); None when the person is not in it."""
    person=next(x for x in p['people'] if x['id']==pid);other=find_person(ref,person)
    return None if other is None else diff_value(p,pid,year,month,ref,other,True,True)

def month_leave_value(p,pid,year,month):
    """All annual-leave days of the month together vs. leaving them unused; None when the month has no 연가."""
    person=next(x for x in p['people'] if x['id']==pid)
    days=[d for d in dates(year,month) if leave_days(p,event(p,person,d)['code'])>0]
    return leave_compare(p,pid,year,month,days) if days else None

def save_project(p,path):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,suffix='.tmp')
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as f:json.dump(p,f,ensure_ascii=False,indent=2)
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def load_project(path):
    p=json.loads(Path(path).read_text(encoding='utf-8'))
    if p.get('version')!=1 or not p.get('people'):raise ValueError('지원하지 않는 설정 파일입니다.')
    for person in p['people']:calculate(p,person['id'],2026,9)
    return p

def demo_project():
    p=empty_project();p['people']=[{'id':'demo','name':'홍길동 (예제)','grade':'일반6','team':'1팀','salary':3580000}]
    p['teams']={'1팀':{'anchor':'2026-09-01','phase':0}};return p
