"""Plain-language 계산 근거 for a comparison card (base plan vs. current plan), shown in a popup.

Every number comes from domain.calculate / diff_value results; this module only lays them out.
"""
from html import escape
from domain import *

DOW='월화수목금토일'
TABLE='<table cellspacing="0" cellpadding="5" style="border-collapse:collapse;margin:4px 0 8px">'
TH='style="background:#edf3fa;border:1px solid #d8e0ea;text-align:center"'
TD='style="border:1px solid #e1e6ed"'
TR='style="border:1px solid #e1e6ed;text-align:right"'

def won(x):return f'{x:,.0f}원'
def signed_won(x):return f'{x:+,.0f}원'
def hours(x):return f'{x:g}시간'

def _table(head,rows):
    out=TABLE+'<tr>'+''.join(f'<th {TH}>{h}</th>' for h in head)+'</tr>'
    for row in rows:out+='<tr>'+''.join(f'<td {TD if i==0 else TR}>{c}</td>' for i,c in enumerate(row))+'</tr>'
    return out+'</table>'

def _summary(cv):
    """One plain sentence explaining why the result is a gain or a loss."""
    gain,comp,net,days=cv['gain'],cv['comp'],cv['net'],cv['leave_days']
    if not days or not comp:
        return f'수당이 {won(abs(gain))} {"늘어" if gain>0 else "줄어"} 그대로 {"이득" if net>0 else "손해" if net<0 else "차이 없음"}입니다.' if gain else '수당과 연가 사용이 같아 차이가 없습니다.'
    if days>0:
        lead=f'수당은 {won(abs(gain))} {"늘지만" if gain>0 else "줄고" if gain<0 else "그대로인데"}, 연가를 {days:g}일 더 써서 나중에 받을 수 있던 연가보상비 {won(comp)}을 못 받게 됩니다.'
    else:
        lead=f'수당은 {won(abs(gain))} {"늘고" if gain>0 else "줄지만" if gain<0 else "그대로이고"}, 연가를 {-days:g}일 덜 써서 연가보상비 {won(-comp)}을 더 받을 수 있습니다.'
    return lead+f' 합치면 <b>{signed_won(net)}</b>, {"이득" if net>0 else "손해" if net<0 else "차이 없음"}입니다.'

def hours_effect(cv):
    """How much of a change in worked hours actually reached overtime pay.

    When the month sits below zero, hours removed (or added) mostly just move the negative balance, so a
    shorter month costs far less than the same hours at the overtime rate. 'absorbed' counts only the part
    taken by the negative balance (not the holiday deduction). None when hours barely changed."""
    b,a=cv['before'],cv['after'];dh=a['raw']-b['raw']
    if abs(dh)<1:return None
    paid=max(0,a['signed'])-max(0,b['signed'])
    return dict(hours=dh,paid=paid,absorbed=(a['signed']-b['signed'])-paid,rate=a['rates'][0],before=b['signed'],after=a['signed'])

def hours_sentence(cv):
    """Plain sentence for hours_effect, or '' when the negative balance absorbed less than an hour."""
    h=hours_effect(cv)
    if not h or abs(h['absorbed'])<1:return ''
    move=f'초과 {h["before"]:+g} → {h["after"]:+g}시간'
    if h['hours']<0:
        lost=-cv['net'];per=f', 줄인 근무 1시간당 약 {won(lost/-h["hours"])}' if lost>0 else ''
        return (f'근무를 {-h["hours"]:g}시간 줄였지만 시간외에 반영된 것은 {-h["paid"]:g}시간뿐입니다({move}, 나머지는 마이너스로 흡수). '
                f'그래서 손해는 {won(max(lost,0))}{per}으로, 같은 시간을 시간외 단가({won(h["rate"])}/시간)로 잃는 것보다 훨씬 작습니다.')
    per=f' 늘린 근무 1시간당 실익은 약 {won(cv["net"]/h["hours"])}입니다(시간외 단가 {won(h["rate"])}).' if cv['net']>0 else ''
    return f'근무를 {h["hours"]:g}시간 늘렸지만 시간외로 받는 것은 {h["paid"]:g}시간뿐입니다({move}, 나머지는 마이너스를 메우는 데 쓰임).'+per

def _hours_note(b,a):
    if b['signed']<0 and a['signed']<0:
        return f'두 경우 모두 초과시간이 마이너스({b["signed"]:+g} → {a["signed"]:+g})라 시간외수당은 0원입니다. 늘거나 줄어든 시간은 마이너스 칸을 채우거나 비우는 데만 쓰여 돈으로 바뀌지 않습니다.'
    if b['signed']<0<=a['signed']:
        return f'기준은 {-b["signed"]:g}시간 모자랐습니다. 늘어난 시간 중 그만큼은 마이너스를 채우는 데 쓰이고, 남은 {a["signed"]:g}시간만 시간외수당으로 받습니다.'
    if a['signed']<0<=b['signed']:
        return f'지금은 초과시간이 {a["signed"]:+g}시간으로 마이너스가 되어 시간외수당이 0원이 됩니다.'
    return '두 경우 모두 초과시간이 0 이상이라 차이 나는 시간만큼 시간외수당이 바뀝니다.'

def explain_html(p,pid,year,month,cv,base_name,base_desc):
    person=next(x for x in p['people'] if x['id']==pid);bp,bperson,bscen,broster=cv['base'];b,a=cv['before'],cv['after']
    hs_b,hs_a=holiday_map(bp,year),holiday_map(p,year);rb,ra=b['rates'],a['rates']
    out=f'<h2 style="margin:0 0 4px">{year}년 {month}월 · {escape(person["name"])} · 손익 계산 근거</h2>'
    color='#107c41' if cv['net']>0 else '#c23b46' if cv['net']<0 else '#5b6676'
    extra=hours_sentence(cv)
    out+=f'<div style="background:#f4f6fa;padding:10px;margin:8px 0"><span style="font-size:18px;font-weight:700;color:{color}">{signed_won(cv["net"])}</span> &nbsp; {_summary(cv)}'+(f'<br><span style="color:#1f5f99">{extra}</span>' if extra else '')+'</div>'
    out+=f'<h3>무엇을 비교했나</h3><p><b>기준</b>: {escape(base_name)} — {base_desc}<br><b>지금</b>: 현재 달력 (저장하지 않은 수정 포함)</p>'

    rows=[]
    for d,_,_ in cv['changed']:
        eo,en=event(bp,bperson,d,bscen,broster),event(p,person,d)
        co,_,no,ho=day_parts(bp,bperson,eo,d,hs_b);cn,_,nn,hn=day_parts(p,person,en,d,hs_a)
        tag=f'{d.day}일({DOW[d.weekday()]})'+(f' <span style="color:#c23b46">{escape(hs_a.get(str(d),""))}</span>' if str(d) in hs_a else '')
        rows.append([tag,escape(eo['code']),escape(en['code']),f'{co:g} → {cn:g} ({cn-co:+g})',f'{no:g} → {nn:g}',f'{ho:g} → {hn:g}'])
    if rows:out+='<h3>바뀐 날짜</h3>'+_table(['날짜','기준','지금','근무 인정시간','야간시간','휴일근무(일)'],rows)
    else:out+='<h3>바뀐 날짜</h3><p>날짜별 근무는 같습니다. 금액 차이는 설정(단가·호봉·월 조정값 등) 차이에서 생깁니다.</p>'

    out+='<h3>① 근무시간 → 초과시간</h3>'
    floor=lambda r:f'{r["total"]:g}'+(f' <span style="color:#8a94a3">({r["raw"]:g} 소수점 버림)</span>' if r['raw']!=r['total'] else '')
    rows=[['한 달 총 근무 인정시간',floor(b),floor(a),f'{a["total"]-b["total"]:+g}'],
          [f'− 정규근무 (평일 {a["base_days"]}일 × 8시간)',hours(b['base_days']*8),hours(a['base_days']*8),f'{(a["base_days"]-b["base_days"])*8:+g}'],
          ['− 휴일근무 공제 (휴일근무일 × 8시간)',f'{b["holiday"]:g}일 × 8 = {hours(b["holiday"]*8)}',f'{a["holiday"]:g}일 × 8 = {hours(a["holiday"]*8)}',f'{(a["holiday"]-b["holiday"])*8:+g}']]
    if a['basic'] or b['basic']:rows.append(['+ 월 기본시간 가산',hours(b['basic']),hours(a['basic']),f'{a["basic"]-b["basic"]:+g}'])
    rows+=[['<b>= 초과 인정시간</b>',f'<b>{b["signed"]:+g}</b>',f'<b>{a["signed"]:+g}</b>',f'<b>{a["signed"]-b["signed"]:+g}</b>'],
           ['시간외로 받는 시간 (마이너스면 0)',hours(max(0,b['signed'])),hours(max(0,a['signed'])),f'{max(0,a["signed"])-max(0,b["signed"]):+g}']]
    out+=_table(['항목','기준','지금','차이(시간)'],rows)
    out+=f'<p>정규근무 시간까지는 봉급에 포함된 시간이라 빼고, 휴일 주간근무 8시간은 휴일근무수당으로 따로 받으므로 다시 세지 않도록 뺍니다. {_hours_note(b,a)}</p>'

    out+='<h3>② 수당</h3>'
    pay=lambda h,rate,amount:f'{h:g} × {rate:,.0f} = {won(amount)}'
    rows=[['시간외수당 (시간 × 시간외 단가)',pay(max(0,b['signed']),rb[0],b['overtime']),pay(max(0,a['signed']),ra[0],a['overtime']),signed_won(a['overtime']-b['overtime'])],
          ['야간수당 (22~06시 시간 × 야간 단가)',pay(b['night'],rb[1],b['night_pay']),pay(a['night'],ra[1],a['night_pay']),signed_won(a['night_pay']-b['night_pay'])],
          ['휴일근무수당 (일수 × 휴일 단가)',pay(b['holiday'],rb[2],b['holiday_pay']),pay(a['holiday'],ra[2],a['holiday_pay']),signed_won(a['holiday_pay']-b['holiday_pay'])],
          ['<b>수당 합계</b>',f'<b>{won(b["pay"])}</b>',f'<b>{won(a["pay"])}</b>',f'<b>{signed_won(cv["gain"])}</b>']]
    out+=_table(['수당','기준','지금','차이'],rows)+'<p>각 수당은 10원 미만을 버립니다. 야간·휴일수당은 초과시간이 마이너스여도 따로 받습니다.</p>'

    out+='<h3>③ 연가보상비 (연가를 안 쓰면 받는 돈)</h3>'
    salary,basis=salary_of(person,p);c=p.get('leave_comp',LEAVE_COMP)
    out+='<p>그해에 쓰지 않은 연가는 연가보상비로 돈으로 받을 수 있습니다. 그래서 연가를 하루 더 쓰면 그만큼 보상비를 포기하는 것이고, 하루 덜 쓰면 그만큼 더 받는 것입니다.</p>'
    if not person.get('leave_comp',True):daily_line='연가보상 대상 아님(N)으로 설정되어 보상비를 0원으로 봅니다. (보상 한도를 넘었거나 어차피 없어지는 연가)'
    elif not salary:daily_line='호봉 또는 본봉이 없어 보상비를 계산하지 못했습니다. 상단 "사용자·호봉"에서 입력하세요.'
    else:daily_line=f'연가보상비 1일 = 월 봉급 {won(salary)} ({escape(basis)}) × {c["rate"]*100:g}% ÷ {c["divisor"]:g} = <b>{won(cv["daily"])}</b>'
    rows=[['연가 사용일수',f'{cv["leave_before"]:g}일',f'{cv["leave_after"]:g}일',f'{cv["leave_days"]:+g}일'],
          ['연가보상비 영향','','',f'<b>{signed_won(-cv["comp"])}</b>']]
    out+=f'<p>{daily_line}</p>'+_table(['항목','기준','지금','차이'],rows)
    out+='<p style="color:#5b6676">연가 사용일수는 연가(주연·야연·당연)만 셉니다. 특별휴가·공가·병가는 연가를 줄이지 않아 보상비와 관계없습니다.</p>'

    out+='<h3>④ 최종 손익</h3>'+_table(['항목','금액'],[['수당 차이 (②)',signed_won(cv['gain'])],['연가보상비 영향 (③)',signed_won(-cv['comp'])],[f'<b>= 손익</b>',f'<b style="color:{color}">{signed_won(cv["net"])}</b>']])
    out+=('<p style="color:#5b6676">참고: 계획용 계산입니다. 월 시간 소수점 버림·10원 미만 버림은 제공된 엑셀 산식을 따랐고, 연가보상비는 원 미만을 반올림해 표시했습니다. '
          '실제 연가보상비는 보상 한도 일수, 지급 기준일 당시 호봉, 기관 지침에 따라 달라질 수 있습니다. 근거는 상단 ? 도움말을 보세요.</p>')
    return out
