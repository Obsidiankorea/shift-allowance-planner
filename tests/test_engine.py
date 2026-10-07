import unittest, datetime as dt, tempfile, os, glob, copy, math
from pathlib import Path
from domain import *
from importer import import_excel

class EngineTest(unittest.TestCase):
    def setUp(self):self.p=demo_project();self.id='demo'
    def test_cycle_continues_across_month_and_year(self):
        for date in [dt.date(2026,10,1),dt.date(2027,1,1),dt.date(2028,2,29)]:
            self.assertEqual(event(self.p,self.p['people'][0],date)['code'],CYCLE[(date-dt.date(2026,9,1)).days%4])
    def test_negative_and_crossing(self):
        r=calculate(self.p,self.id,2026,9);self.p['adjustments']={self.id:{'2026-09':{'hours':-r['signed']-50}}}
        a=calculate(self.p,self.id,2026,9);b=calculate(self.p,self.id,2026,9,extra=8);c=calculate(self.p,self.id,2026,9,extra=51)
        self.assertEqual(a['signed'],-50);self.assertEqual(b['overtime'],0);self.assertEqual(c['signed'],1);self.assertEqual(a['night_pay'],b['night_pay']);self.assertEqual(a['holiday_pay'],b['holiday_pay'])
    def test_fraction_floor(self):
        a=calculate(self.p,self.id,2026,9,extra=.5);b=calculate(self.p,self.id,2026,9,extra=1)
        self.assertEqual(b['total']-a['total'],1)
    def test_user_isolation_and_roundtrip(self):
        self.p['people'].append(dict(id='second',name='나',grade='일반6',team='1팀',salary=0))
        self.p['overrides']={'demo':{'2026-11-01':{'code':'교육','hours':6}}}
        self.assertNotEqual(event(self.p,self.p['people'][1],dt.date(2026,11,1))['code'],'교육')
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'save.json');save_project(self.p,path);self.assertEqual(load_project(path),self.p)
    def test_leave_distinction(self):self.assertEqual(leave_credit('야연','소방경'),8);self.assertEqual(leave_credit('야연','일반6'),15)
    def test_holiday_override(self):
        a=calculate(self.p,self.id,2026,9);self.p['holidays_add']['2026-09-01']='임시';b=calculate(self.p,self.id,2026,9)
        self.assertEqual(a['base_days']-b['base_days'],1);self.assertEqual(b['holiday']-a['holiday'],1)
    def test_replace_vs_add(self):
        d='2026-09-01';a=calculate(self.p,self.id,2026,9)
        self.p['overrides']={self.id:{d:{'code':'주','education':8}}};b=calculate(self.p,self.id,2026,9)
        self.assertEqual(b['raw']-a['raw'],8)
        self.p['overrides'][self.id][d]={'code':'교육','hours':8};c=calculate(self.p,self.id,2026,9);self.assertEqual(c['raw']-a['raw'],-1.5)
    def test_salary_table_and_leave_comp(self):
        person=self.p['people'][0]
        self.assertEqual(salary_of(person),(3580000.0,'직접 입력 본봉'))
        person['step']=11;self.assertEqual(salary_of(person)[0],3580000)
        for g,step,pay in [('일반6',14,3890000),('일반7',14,3514500),('일반8',18,3444300),('일반9',17,3070800),('소방경',14,4261000),('소방위',14,3909100),('일반6',32,4967800)]:
            self.assertEqual(salary_of({'grade':g,'step':step})[0],pay)
        self.assertAlmostEqual(leave_comp_daily(self.p,person),3580000*.86/30)
        person['leave_comp']=False;self.assertEqual(leave_comp_daily(self.p,person),0)
    def leave_on_night(self,code='야연'):
        person=self.p['people'][0];d=next(x for x in dates(2026,10) if event(self.p,person,x)['code']=='야' and x.weekday()<5)
        self.p['overrides']={self.id:{str(d):{'code':code}}};return d
    def test_leave_day_vs_unused_negative_month(self):
        # User example: overtime -10h without the leave; 일반직 야연 adds 15h -> only 5h payable, less than one day's 연가보상비.
        d=self.leave_on_night();empty=calculate(with_code(self.p,self.id,d,'비'),self.id,2026,10)
        self.p['adjustments']={self.id:{'2026-10':{'hours':-10-empty['signed']}}}
        before=copy.deepcopy(self.p);lv=leave_value(self.p,self.id,d);self.assertEqual(self.p,before)
        self.assertEqual((lv['code'],lv['without'],lv['signed'],lv['hours'],lv['days']),('야연',-10,5,15,1));self.assertEqual(lv['gain'],68460)
        self.assertAlmostEqual(lv['net'],68460-3580000*.86/30);self.assertLess(lv['net'],0);self.assertAlmostEqual(lv['need_hours'],3580000*.86/30/13692)
        self.p['adjustments'][self.id]['2026-10']['hours']+=10;self.assertGreater(leave_value(self.p,self.id,d)['net'],0)
        self.p['people'][0]['grade']='소방위';self.assertEqual(leave_value(self.p,self.id,d)['hours'],8)
        self.p['people'][0]['leave_comp']=False;lv=leave_value(self.p,self.id,d);self.assertEqual((lv['comp'],lv['net']),(0,lv['gain']))
    def test_leave_compare_only_for_annual_leave(self):
        person=self.p['people'][0];d=self.leave_on_night('야특')
        self.assertIsNone(leave_value(self.p,self.id,d));self.assertIsNone(month_leave_value(self.p,self.id,2026,10))
        work=next(x for x in dates(2026,10) if event(self.p,person,x)['code']=='주');self.assertIsNone(leave_value(self.p,self.id,work))
        self.p['overrides'][self.id][str(d)]={'code':'야연'};self.p['overrides'][self.id][str(work)]={'code':'주연'}
        mv=month_leave_value(self.p,self.id,2026,10);self.assertEqual((mv['days'],sorted(mv['dates'])),(2,sorted([d,work])))
        self.assertEqual(mv['hours'],23);self.assertAlmostEqual(mv['comp'],2*3580000*.86/30)
        self.p['overrides'][self.id][str(work)]={'code':'당연2'};self.assertEqual(month_leave_value(self.p,self.id,2026,10)['days'],3)
    def test_old_save_without_leave_settings(self):
        for k in ['leave_days','leave_comp']:self.p.pop(k)
        d=self.leave_on_night();self.assertEqual(leave_value(self.p,self.id,d)['days'],1)
    def test_month_formula_parts(self):
        self.p['adjustments']={self.id:{'2026-10':{'basic':3}}};r=calculate(self.p,self.id,2026,10)
        self.assertEqual(r['basic'],3);self.assertEqual(r['signed'],math.trunc(r['total']-r['base_days']*8-r['holiday']*8+3))
    def test_change_vs_untouched_roster(self):
        person=self.p['people'][0];self.assertEqual(change_value(self.p,self.id,2026,10)['changed'],[])
        night=next(x for x in dates(2026,10) if event(self.p,person,x)['code']=='야' and x.weekday()<5)
        rest=next(x for x in dates(2026,10) if event(self.p,person,x)['code']=='비' and x.weekday()==5)
        self.p['overrides']={self.id:{str(night):{'code':'야연'},str(rest):{'code':'주'}}}
        cv=change_value(self.p,self.id,2026,10);before=calculate(self.p,self.id,2026,10,False);after=calculate(self.p,self.id,2026,10)
        self.assertEqual(cv['changed'],[(night,'야','야연'),(rest,'비','주')] if night<rest else [(rest,'비','주'),(night,'야','야연')])
        self.assertEqual(cv['gain'],after['pay']-before['pay']);self.assertEqual(cv['leave_days'],1)
        self.assertAlmostEqual(cv['net'],cv['gain']-3580000*.86/30);self.assertEqual(after['holiday']-before['holiday'],1)
        self.p['baseline'][self.id]={str(night):{'code':'야연'}};self.assertEqual(change_value(self.p,self.id,2026,10)['leave_days'],0)
        self.p['overrides'][self.id][str(night)]={'code':'야'};self.assertEqual(change_value(self.p,self.id,2026,10)['leave_days'],-1)
    def test_change_vs_cycle_and_vs_roster(self):
        # Excel month already has a leave and a swap; my extra edit is compared separately.
        person=self.p['people'][0];ds=dates(2026,10);work=next(x for x in ds if event(self.p,person,x)['code']=='주' and x.weekday()<5)
        rest=next(x for x in ds if event(self.p,person,x)['code']=='비' and x.weekday()==5);night=next(x for x in ds if event(self.p,person,x)['code']=='야' and x>rest)
        self.p['baseline'][self.id]={str(d):{'code':event(self.p,person,d)['code']} for d in ds};self.p['baseline'][self.id][str(work)]={'code':'주연','memo':'엑셀 비고'};self.p['baseline'][self.id][str(rest)]={'code':'주'}
        self.assertTrue(has_roster(self.p,self.id,2026,10));self.assertFalse(has_roster(self.p,self.id,2026,11))
        self.assertEqual(change_value(self.p,self.id,2026,10)['changed'],[])
        cyc=change_value(self.p,self.id,2026,10,'cycle');self.assertEqual([c[0] for c in cyc['changed']],sorted([work,rest]));self.assertEqual(cyc['leave_days'],1)
        self.assertEqual(cyc['before'],calculate(self.p,self.id,2026,10,False,roster=False))
        self.p['overrides']={self.id:{str(night):{'code':'야연'}}}
        ros=change_value(self.p,self.id,2026,10);self.assertEqual(ros['changed'],[(night,'야','야연')]);self.assertEqual(ros['leave_days'],1)
        cyc=change_value(self.p,self.id,2026,10,'cycle');self.assertEqual(len(cyc['changed']),3);self.assertEqual(cyc['leave_days'],2)
        self.assertEqual(cyc['gain']-ros['gain'],calculate(self.p,self.id,2026,10,False)['pay']-calculate(self.p,self.id,2026,10,False,roster=False)['pay'])
        strip=lambda r:{k:v for k,v in r.items() if k!='base'};self.assertEqual(strip(change_value(self.p,self.id,2026,11,'cycle')),strip(change_value(self.p,self.id,2026,11)))
    def test_saved_file_comparison(self):
        person=self.p['people'][0];ds=dates(2026,10);night=next(x for x in ds if event(self.p,person,x)['code']=='야' and x.weekday()<5)
        with tempfile.TemporaryDirectory() as d:
            path=os.path.join(d,'10-07 홍길동 수정.json');self.p['overrides']={self.id:{str(night):{'code':'야연'}}};save_project(self.p,path);ref=load_project(path)
        self.assertEqual(file_change_value(self.p,self.id,2026,10,ref)['changed'],[])
        rest=next(x for x in ds if event(self.p,person,x)['code']=='비' and x.weekday()==5);self.p['overrides'][self.id][str(rest)]={'code':'주'}
        cv=file_change_value(self.p,self.id,2026,10,ref);self.assertEqual(cv['changed'],[(rest,'비','주')]);self.assertEqual(cv['leave_days'],0)
        self.assertEqual(cv['gain'],calculate(self.p,self.id,2026,10)['pay']-calculate(ref,self.id,2026,10)['pay'])
        self.p['overrides'][self.id].pop(str(night));self.assertEqual(file_change_value(self.p,self.id,2026,10,ref)['leave_days'],-1)
        other=copy.deepcopy(ref);other['people'][0]['id']='renamed';self.assertEqual(find_person(other,person)['id'],'renamed')
        other['people'][0]['name']='다른 사람';self.assertIsNone(file_change_value(self.p,self.id,2026,10,other))
    def test_project_salary_table_and_year_check(self):
        person=dict(self.p['people'][0],step=11)
        self.assertFalse(salary_outdated(self.p,dt.date(2026,12,31)));self.assertTrue(salary_outdated(self.p,dt.date(2027,1,1)))
        self.p['salary_table']={'year':2027,'table':{'일반6':[100]*11+[3700000]}}
        self.assertFalse(salary_outdated(self.p,dt.date(2027,1,1)));self.assertEqual(salary_of(person,self.p)[0],100);self.assertEqual(salary_of(dict(person,step=12),self.p),(3700000.0,'2027 봉급표 일반6 12호봉'))
        self.assertEqual(salary_of(person)[0],3580000);self.assertAlmostEqual(leave_comp_daily(self.p,dict(person,step=12)),3700000*.86/30)
    def test_explain_popup_numbers(self):
        from explain import explain_html
        d=self.leave_on_night();empty=calculate(with_code(self.p,self.id,d,'비'),self.id,2026,10)
        self.p['adjustments']={self.id:{'2026-10':{'hours':-10-empty['signed']}}};lv=leave_value(self.p,self.id,d)
        html=explain_html(self.p,self.id,2026,10,lv,'연가 안 쓰고 비움','설명')
        for text in ['68,460원','102,627원','-34,167원','3,580,000원','손해','10시간 모자랐습니다','남은 5시간만']:self.assertIn(text,html)
        cv=change_value(self.p,self.id,2026,10,'cycle');self.assertIn('야연',explain_html(self.p,self.id,2026,10,cv,'주야비비','설명'))
    def test_guide_documents_render(self):
        import documents
        intro,src=documents.render('시작 안내');self.assertEqual([p.stem for p in src],['시작 안내','만든 이유','훈령 제5조 정리','근거 계통 요약'])
        self.assertIn('href="doc:'+documents.quote('여름휴가 검토')+'"',intro);self.assertIn('<table',intro)
        help_html,src=documents.render('도움말');self.assertIn('산식과 근거',[p.stem for p in src]);self.assertIn('<span style="color:#107c41">[공식 확인]</span>',help_html)
        summer,_=documents.render(documents.doc_target(documents.quote('여름휴가 검토')))
        for text in ['제4항 제2호','제4항 제3호','제4항 제4호','원칙적으로 연가 사용 불필요','지방공무원 복무규정」 제5조','유권해석','<pre']:self.assertIn(text,summer)
        self.assertIn('찾을 수 없습니다',documents.render('없는 문서')[0])
        with tempfile.TemporaryDirectory() as d:
            old=documents.doc_dir;documents.doc_dir=lambda:Path(d)
            try:
                Path(d,'a.md').write_text('---\ntags: x\n---\n![[b]] [[c|씨]]',encoding='utf-8');Path(d,'b.md').write_text('![[a]] B본문',encoding='utf-8')
                html,src=documents.render('a');self.assertIn('B본문',html);self.assertIn('반복',html);self.assertIn('>씨</a>',html);self.assertNotIn('tags:',html)
            finally:documents.doc_dir=old
    def test_updater_versions_and_swap(self):
        import updater
        self.assertTrue(updater.newer('v1.10.0','1.9.3'));self.assertFalse(updater.newer('1.8.0','1.8.0'));self.assertFalse(updater.newer('','1.8.0'))
        with tempfile.TemporaryDirectory() as d:
            app,new=Path(d,'교대근무수당플래너'),Path(d,'staged')
            for folder,text in [(app,'old'),(new,'new')]:(folder/'문서').mkdir(parents=True);(folder/'문서'/'만든 이유.md').write_text(text,encoding='utf-8');(folder/'교대근무수당플래너.exe').write_text(text)
            exe=updater.finish(app,0,source=new)
            self.assertEqual(exe.read_text(),'new');self.assertEqual((app/'문서'/'만든 이유.md').read_text(encoding='utf-8'),'new')
            self.assertEqual((Path(d,'교대근무수당플래너_이전버전')/'문서'/'만든 이유.md').read_text(encoding='utf-8'),'old')
    def test_supplied_workbooks(self):
        legacy=os.environ.get('TEST_ALLOWANCE_XLSX');schedule=os.environ.get('TEST_SCHEDULE_XLSX')
        if not legacy or not schedule:self.skipTest('실제 파일 경로 미지정')
        p=import_excel(legacy);self.assertEqual(len(p['people']),12)
        for person in p['people']:
            result=calculate(p,person['id'],2026,9)
            for k,v in p['reference'][person['id']].items():self.assertEqual(result[k],v,(person['name'],k))
        p=import_excel(schedule);self.assertEqual(len(p['people']),12)
        main=os.environ.get('TEST_MAIN_PERSON')  # optional: name of the user whose October was hand-checked (signed 0, 11/1 야)
        ji=next((x for x in p['people'] if x['name']==main),None)
        if ji:self.assertEqual(calculate(p,ji['id'],2026,10)['signed'],0);self.assertEqual(event(p,ji,dt.date(2026,11,1))['code'],'야')
        for x in p['people']:  # months without Excel continue each team's 주야비비 cycle
            t=p['teams'][x['team']];self.assertEqual(event(p,x,dt.date(2026,11,1))['code'],CYCLE[((dt.date(2026,11,1)-dt.date.fromisoformat(t['anchor'])).days+t['phase'])%4])
        doubles=[x for x in p['people'] if event(p,x,dt.date(2026,10,1)).get('credit')==25];self.assertEqual(len(doubles),1)  # one 주+야 double duty on 10/1

if __name__=='__main__':unittest.main()
