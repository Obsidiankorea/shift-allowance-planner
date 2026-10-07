from __future__ import annotations
import sys, os, re, csv, copy, json, math, subprocess, threading, datetime as dt
from pathlib import Path
from PySide6.QtCore import Qt,QDate,QLocale,QRect,QTimer,QUrl
from PySide6.QtGui import QColor,QFont,QPainter,QKeySequence,QAction,QDesktopServices
from PySide6.QtWidgets import *
from domain import *
from importer import import_excel
from documents import render, doc_dir, doc_path, doc_target
from version import VERSION, REPO
from urllib.parse import quote
import updater
from explain import explain_html, hours_effect
from html import escape

STORE=Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'.local/share')))/'ShiftAllowancePlanner'/'autosave.json'
def prefs_path():return STORE.with_name('prefs.json')

def load_prefs():
    """Per-PC UI choices (last file, comparison file, start user); kept apart from the shared project JSON."""
    try:return json.loads(prefs_path().read_text(encoding='utf-8'))
    except Exception:return {}

def snapshot(p):return json.loads(json.dumps(p,ensure_ascii=False))

def spin(v=0,maximum=1000):
    s=QDoubleSpinBox();s.setRange(0,maximum);s.setDecimals(2);s.setSingleStep(.5);s.setValue(v);return s

def buttons(dialog,layout):
    b=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel);b.button(QDialogButtonBox.StandardButton.Ok).setText('확인');b.button(QDialogButtonBox.StandardButton.Cancel).setText('취소');b.accepted.connect(dialog.accept);b.rejected.connect(dialog.reject);layout.addWidget(b)

class DayDialog(QDialog):
    def __init__(self,e,date,parent):
        super().__init__(parent);self.original=e;self.setWindowTitle(f'{date} 근무 편집');self.resize(470,600);v=QVBoxLayout(self);f=QFormLayout();v.addLayout(f)
        self.code=QComboBox();self.code.addItems(CODES);self.code.setCurrentText(e['code']);f.addRow('당일 기본 근무',self.code)
        self.fields={}
        for key,label,default in [('hours','교육·출장으로 대체한 시간',8),('education','기존 근무에 추가할 교육시간',0),('travel','기존 근무에 추가할 출장시간',0),('extra','기타 추가 근무시간',0),('activity','엑셀 비번활동 인정시간',0),('deduction','제외할 시간',0)]:
            s=spin(e.get(key,default),48);self.fields[key]=s;f.addRow(label,s)
        self.custom=QCheckBox('당일 인정시간 직접 지정 (복합 근무 등)');self.custom.setChecked('credit' in e);f.addRow(self.custom)
        for key,label,default in [('credit','총근무 산입시간 직접 지정',0),('night','야간수당 인정시간 직접 지정',0),('holiday','휴일수당 일수 직접 지정',0)]:
            s=spin(e.get(key,default),48);self.fields[key]=s;f.addRow(label,s)
        self.memo=QLineEdit(e.get('memo',''));f.addRow('메모',self.memo)
        label=QLabel('교육/출장으로 근무를 바꾸면 기본 근무시간을 대체합니다.\n추가 교육/출장은 기본 근무를 유지하며 더합니다.\n교육 산입은 지급 승인 여부와 별개인 시뮬레이션입니다.');label.setWordWrap(True);v.addWidget(label);buttons(self,v)
    def result_event(self):
        e={'code':self.code.currentText(),'memo':self.memo.text()}
        for k,s in self.fields.items():
            if k not in ['credit','night','holiday'] or self.custom.isChecked():e[k]=s.value()
        return e

class Calendar(QCalendarWidget):
    def __init__(self,owner):
        super().__init__();self.owner=owner;self.setLocale(QLocale('ko_KR'));self.setVerticalHeaderFormat(QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader);self.setGridVisible(False);self.setMinimumSize(650,450)
        nav=self.findChild(QWidget,'qt_calendar_navigationbar');self.regular=QLabel();self.regular.setObjectName('regular')
        nav.layout().insertWidget(nav.layout().indexOf(self.findChild(QToolButton,'qt_calendar_yearbutton'))+1,self.regular)
    def paintCell(self,painter,rect,date):
        o=self.owner
        if not o.p['people']:return super().paintCell(painter,rect,date)
        d=date.toPython();person=o.person();e=event(o.p,person,d);code=e['code'];outside=date.month()!=self.monthShown()
        colors={'주':'#e5f2ff','야':'#eae6ff','당':'#eae6ff','비':'#ffffff','교육':'#fff0cd','출장':'#fff0cd'}
        bg=colors.get(code,'#e0f4ed');painter.save();painter.fillRect(rect.adjusted(3,3,-3,-3),QColor(bg if not outside else '#f5f6f8'))
        if date==self.selectedDate():painter.setPen(QColor('#0078d4'));painter.drawRoundedRect(rect.adjusted(3,3,-4,-4),6,6)
        painter.setPen(QColor('#999999' if outside else '#c23b46' if d.weekday()>=5 or str(d) in o.holidays else '#263347'))
        painter.drawText(rect.adjusted(10,6,-6,-6),Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignLeft,str(d.day))
        painter.setPen(QColor('#84909f' if outside else '#263347'));painter.drawText(rect.adjusted(5,16,-5,-4),Qt.AlignmentFlag.AlignCenter,code)
        extras=sum(float(e.get(k,0)) for k in ['education','travel','extra','activity'])
        footer=(f'+{extras:g}h ' if extras else '')+('수정' if str(d) in o.p['overrides'].get(person['id'],{}) else '')
        painter.setFont(QFont('Malgun Gothic',8));painter.drawText(rect.adjusted(5,3,-5,-7),Qt.AlignmentFlag.AlignBottom|Qt.AlignmentFlag.AlignHCenter,footer);painter.restore()

class Settings(QDialog):
    def __init__(self,p,parent,tab=None):
        super().__init__(parent);self.p=copy.deepcopy(p);self.setWindowTitle('사용자 · 근무주기 · 단가 · 봉급표 · 공휴일 설정');self.resize(900,680);v=QVBoxLayout(self);tabs=QTabWidget();v.addWidget(tabs)
        self.tables={}
        datasets=[('people','사용자',['이름','직급','소속 조','호봉 (봉급표, 비우면 직접입력)','본봉 직접입력','연가보상 대상 Y/N'],[[x['name'],x['grade'],x['team'],x.get('step') or '',x.get('salary',0),'Y' if x.get('leave_comp',True) else 'N'] for x in p['people']]),('teams','4교대 주기',['조 이름','기준일 YYYY-MM-DD','순서 0=주 1=야 2=비1 3=비2'],[[k,x['anchor'],x['phase']] for k,x in p['teams'].items()]),('work_hours','근무시간',['근무 코드','산입 시간'],[[k,v] for k,v in p.get('work_hours',WORK).items()]),('leave_hours','야간연가',['구분','산입 시간'],[[k,v] for k,v in p.get('leave_hours',{'일반직 야연':15,'소방직 야연':8}).items()]),('leave_days','연가 차감일수',['연가 코드','사용 연가일수 (보상비 기회비용)'],[[k,v] for k,v in p.get('leave_days',LEAVE_DAYS).items()]),('rates','수당 단가',['직급','시간외 원/시간','야간 원/시간','휴일 원/일'],[[k,*r] for k,r in p['rates'].items()])]
        for key,title,heads,rows in datasets:
            t=QTableWidget(len(rows),len(heads));t.setHorizontalHeaderLabels(heads);t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
            for i,row in enumerate(rows):
                for j,val in enumerate(row):t.setItem(i,j,QTableWidgetItem(str(val)))
            self.tables[key]=t
            if key=='people':
                for i,person in enumerate(p['people']):t.item(i,0).setData(Qt.ItemDataRole.UserRole,person['id'])
            wrap=QWidget();box=QVBoxLayout(wrap);box.addWidget(t)
            if key in ['people','teams','rates']:
                tools=QHBoxLayout();box.addLayout(tools);add=QPushButton('행 추가');delete=QPushButton('선택 행 삭제');tools.addWidget(add);tools.addWidget(delete)
                add.clicked.connect(lambda checked=False,table=t:self.add_row(table));delete.clicked.connect(lambda checked=False,table=t:table.removeRow(table.currentRow()) if table.currentRow()>=0 else None)
            tabs.addTab(wrap,title)
        page=QWidget();f=QFormLayout(page);self.round=QCheckBox('엑셀과 동일하게 월 총시간 소수점 절사');self.round.setChecked(p['round_hours']);f.addRow(self.round)
        self.rateyear=QSpinBox();self.rateyear.setRange(2000,2200);self.rateyear.setValue(p['rate_year']);f.addRow('단가 기준연도',self.rateyear)
        comp=p.get('leave_comp',LEAVE_COMP);self.comprate=QDoubleSpinBox();self.comprate.setRange(0,100);self.comprate.setDecimals(2);self.comprate.setValue(float(comp['rate'])*100);f.addRow('연가보상비 월봉급액 비율 (%)',self.comprate)
        self.compdiv=QDoubleSpinBox();self.compdiv.setRange(1,365);self.compdiv.setDecimals(0);self.compdiv.setValue(float(comp['divisor']));f.addRow('연가보상비 1일 환산 분모 (일)',self.compdiv)
        f.addRow(QLabel(f'봉급표: {salary_table(p)[0]}년 (봉급표 탭). 호봉을 입력하면 본봉 직접입력보다 우선합니다.'))
        self.add=QPlainTextEdit('\n'.join(f'{k} {v}' for k,v in p['holidays_add'].items()));self.remove=QPlainTextEdit('\n'.join(p['holidays_remove']));f.addRow('추가 공휴일: 날짜 명칭 (줄별)',self.add);f.addRow('공휴일 제외: 날짜 (줄별)',self.remove)
        ref=QPlainTextEdit('\n'.join(f'{k} {v}' for k,v in p.get('source_holidays',{}).items()));ref.setReadOnly(True);f.addRow('엑셀 휴일 목록 (참고, 자동 적용 안 함)',ref);tabs.addTab(page,'산식 · 공휴일')
        page=QWidget();box=QVBoxLayout(page);top=QHBoxLayout();box.addLayout(top);self.payyear=QSpinBox();self.payyear.setRange(2000,2200);top.addWidget(QLabel('봉급표 연도'));top.addWidget(self.payyear);top.addStretch()
        for label,fn in [('클립보드 표 붙여넣기',self.paste_pay),(f'내장 {SALARY_YEAR}년 표로 되돌리기',lambda:self.fill_pay(SALARY_YEAR,SALARY))]:
            b=QPushButton(label);b.clicked.connect(fn);top.addWidget(b)
        self.pay=QTableWidget();box.addWidget(self.pay);self.fill_pay(*salary_table(p))
        note=QLabel('매년 인사혁신처 봉급표(일반직 별표3, 소방 별표10)를 복사한 뒤 1호봉 첫 칸을 선택하고 붙여넣으세요. 열 순서는 위 직급 순서와 같아야 합니다.\n연도를 올해로 바꿔야 시작할 때의 "봉급표 갱신" 경고가 사라집니다. AI에게 pay_tables.py 갱신을 맡겨도 됩니다.');note.setWordWrap(True);box.addWidget(note)
        pay_tab=tabs.addTab(page,'봉급표')
        if tab=='봉급표':tabs.setCurrentIndex(pay_tab)
        self.owner=parent;prefs=getattr(parent,'prefs',{})
        page=QWidget();f=QFormLayout(page)
        self.editor=QComboBox();self.editor.addItems(EDITORS.values());self.editor.setCurrentIndex(list(EDITORS).index(prefs.get('editor','default')) if prefs.get('editor','default') in EDITORS else 0);f.addRow('문서 편집 프로그램',self.editor)
        row=QHBoxLayout();self.editor_path=QLineEdit(prefs.get('editor_path',''));self.editor_path.setPlaceholderText('직접 지정할 때 실행 파일 경로 (예: Typora.exe, notepad++.exe)');row.addWidget(self.editor_path)
        b=QPushButton('찾아보기');b.clicked.connect(self.pick_editor);row.addWidget(b);f.addRow('',row)
        b=QPushButton('문서 폴더 열기');b.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc_dir()))));f.addRow('',b)
        note=QLabel('안내 창의 ✎ 편집이 이 프로그램으로 문서를 엽니다. 저장하면 열린 안내 창에 바로 반영됩니다.\nObsidian: 문서 폴더를 Obsidian에서 "폴더를 보관소로 열기"로 한 번 등록하세요.');note.setObjectName('muted');note.setWordWrap(True);f.addRow('',note)
        f.addRow(QLabel(''))
        f.addRow('현재 버전',QLabel(f'v{VERSION}  ·  GitHub {REPO}'))
        self.upd_check=QCheckBox('시작할 때 업데이트 확인');self.upd_check.setChecked(prefs.get('update_check',True));f.addRow('',self.upd_check)
        self.upd_auto=QCheckBox('새 버전이 있으면 자동으로 받아 설치 (다시 시작할 때 적용)');self.upd_auto.setChecked(prefs.get('auto_update',False));f.addRow('',self.upd_auto)
        b=QPushButton('지금 업데이트 확인');b.clicked.connect(lambda:parent.check_update(manual=True) if hasattr(parent,'check_update') else None);f.addRow('',b)
        mode_note={'exe':'EXE: GitHub 릴리스의 새 zip을 받아 프로그램 폴더를 바꿉니다. 옛 폴더는 "_이전버전"으로 남습니다.','git':'소스 실행: git pull --ff-only로 받습니다. 받은 뒤 프로그램을 다시 켜야 반영됩니다.','none':'소스 실행이지만 git 저장소가 아니어서 업데이트를 쓸 수 없습니다.'}[updater.mode()]
        note=QLabel(mode_note);note.setObjectName('muted');note.setWordWrap(True);f.addRow('',note)
        doc_tab=tabs.addTab(page,'문서·업데이트')
        if tab=='문서·업데이트':tabs.setCurrentIndex(doc_tab)
        v.addWidget(QLabel('조 이름을 변경할 때 사용자 탭의 소속 조도 함께 변경하세요. 기준일 순서는 이후 모든 미입력 월에 이어집니다.'));buttons(self,v)
    def pick_editor(self):
        path,_=QFileDialog.getOpenFileName(self,'문서 편집 프로그램 선택','','프로그램 (*.exe);;모든 파일 (*)')
        if path:self.editor_path.setText(path);self.editor.setCurrentIndex(list(EDITORS).index('custom'))
    def fill_pay(self,year,table):
        n=max(len(x) for x in table.values());self.payyear.setValue(year);self.pay.clear();self.pay.setRowCount(n);self.pay.setColumnCount(len(table))
        self.pay.setHorizontalHeaderLabels(list(table));self.pay.setVerticalHeaderLabels([f'{i+1}호봉' for i in range(n)])
        for c,values in enumerate(table.values()):
            for r in range(n):self.pay.setItem(r,c,QTableWidgetItem(f'{values[r]:,}' if r<len(values) else ''))
    def paste_pay(self):
        """Paste a copied salary grid (browser/Excel, tab separated) at the selected cell; row labels and header rows are skipped."""
        num=lambda x:re.sub(r'[,\s원]','',x);r0,c0=max(0,self.pay.currentRow()),max(0,self.pay.currentColumn())
        rows=[line.split('\t') for line in QApplication.clipboard().text().splitlines()]
        rows=[cells[1:] if cells and not num(cells[0]).isdigit() else cells for cells in rows if any(num(x).isdigit() for x in cells)]
        if not rows:return QMessageBox.warning(self,'붙여넣기','클립보드에 숫자 표가 없습니다.')
        if r0+len(rows)>self.pay.rowCount():self.pay.setRowCount(r0+len(rows));self.pay.setVerticalHeaderLabels([f'{i+1}호봉' for i in range(self.pay.rowCount())])
        for i,cells in enumerate(rows):
            for j,x in enumerate(cells):
                if c0+j<self.pay.columnCount():self.pay.setItem(r0+i,c0+j,QTableWidgetItem(f'{int(num(x)):,}' if num(x).isdigit() else ''))
    def read_pay(self):
        table={}
        for c in range(self.pay.columnCount()):
            g=self.pay.horizontalHeaderItem(c).text();values=[]
            for r in range(self.pay.rowCount()):
                item=self.pay.item(r,c);x=re.sub(r'[,\s원]','',item.text() if item else '')
                if not x:break
                if not x.isdigit() or int(x)<=0:raise ValueError(f'봉급표 {g} {r+1}호봉 값을 확인하세요.')
                values.append(int(x))
            if values:table[g]=values
        return self.payyear.value(),table
    def add_row(self,t):
        r=t.rowCount();t.insertRow(r)
        for c in range(t.columnCount()):t.setItem(r,c,QTableWidgetItem(''))
    def accept(self):
        try:
            def rows(k):
                t=self.tables[k];return [[t.item(i,j).text().strip() for j in range(t.columnCount())] for i in range(t.rowCount())]
            teams={}
            for name,anchor,phase in rows('teams'):
                dt.date.fromisoformat(anchor);phase=int(phase)
                if phase not in range(4):raise ValueError('순서는 0~3입니다.')
                teams[name]={'anchor':anchor,'phase':phase}
            hours={k:float(v) for k,v in rows('work_hours')};leaves={k:float(v) for k,v in rows('leave_hours')};ldays={k:float(v) for k,v in rows('leave_days') if k}
            if any(v<0 or v>3 for v in ldays.values()):raise ValueError('연가 사용일수는 0~3일로 입력하세요.')
            if any(v<0 or v>48 for v in list(hours.values())+list(leaves.values())):raise ValueError('시간은 0~48로 입력하세요.')
            rates={g:[float(a),float(b),float(c)] for g,a,b,c in rows('rates')}
            if any(v<0 for r in rates.values() for v in r):raise ValueError('단가는 0 이상이어야 합니다.')
            payyear,paytable=self.read_pay();people=[]
            for i,row in enumerate(rows('people')):
                import uuid
                pid=self.tables['people'].item(i,0).data(Qt.ItemDataRole.UserRole) or uuid.uuid4().hex
                person={'id':pid}
                n,g,t,step,s,comp=row
                if not n or g not in rates or t not in teams:raise ValueError('이름, 직급 또는 소속 조를 확인하세요.')
                if float(s or 0)<0:raise ValueError('본봉은 0 이상입니다.')
                step=int(step or 0)
                if step and not 1<=step<=len(paytable.get(g,[])):raise ValueError(f'{n}: {g} 봉급표에 {step}호봉이 없습니다.')
                if comp.upper() not in ['Y','N','']:raise ValueError('연가보상 대상은 Y 또는 N입니다.')
                person.update(name=n,grade=g,team=t,step=step,salary=float(s or 0),leave_comp=comp.upper()!='N');people.append(person)
            if not people:raise ValueError('사용자가 한 명 이상 필요합니다.')
            self.p['people']=people
            if (payyear,paytable)==(SALARY_YEAR,SALARY):self.p.pop('salary_table',None)
            else:self.p['salary_table']={'year':payyear,'table':paytable}
            adds={}
            for line in self.add.toPlainText().splitlines():
                if line.strip():d,*name=line.split();dt.date.fromisoformat(d);adds[d]=' '.join(name) or '추가 공휴일'
            removes=[x.strip() for x in self.remove.toPlainText().splitlines() if x.strip()]
            for d in removes:dt.date.fromisoformat(d)
            self.p.update(work_hours=hours,leave_hours=leaves,leave_days=ldays,leave_comp={'rate':self.comprate.value()/100,'divisor':self.compdiv.value()},teams=teams,rates=rates,round_hours=self.round.isChecked(),rate_year=self.rateyear.value(),holidays_add=adds,holidays_remove=removes)
            editor=list(EDITORS)[self.editor.currentIndex()]
            if editor=='custom' and not os.path.exists(self.editor_path.text().strip()):raise ValueError('직접 지정한 문서 편집 프로그램을 찾을 수 없습니다.')
            if hasattr(self.owner,'prefs'):
                self.owner.prefs.update(editor=editor,editor_path=self.editor_path.text().strip(),update_check=self.upd_check.isChecked(),auto_update=self.upd_auto.isChecked());self.owner.save_prefs()
            super().accept()
        except Exception as e:QMessageBox.warning(self,'입력 확인',str(e))

EDITORS={'default':'Windows 기본 연결 프로그램','obsidian':'Obsidian','custom':'직접 지정'}

def open_in_editor(path,prefs):
    """Open a guide document with the editor chosen in 설정 → 문서·업데이트."""
    path=Path(path);mode=prefs.get('editor','default')
    try:
        if mode=='obsidian':QDesktopServices.openUrl(QUrl('obsidian://open?path='+quote(str(path))))
        elif mode=='custom' and prefs.get('editor_path'):subprocess.Popen([prefs['editor_path'],str(path)])
        elif hasattr(os,'startfile'):os.startfile(str(path))
        else:QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
    except OSError as e:QMessageBox.warning(None,'편집 프로그램',f'문서를 열지 못했습니다: {e}\n설정 → 문서·업데이트에서 편집 프로그램을 확인하세요.')

def html_view(html,owner):
    """Text view for generated HTML (계산 근거): web links open in the browser, doc: links open a guide document."""
    view=QTextBrowser();view.setOpenLinks(False);view.setHtml(html)
    view.anchorClicked.connect(lambda url:DocDialog(owner,doc_target(url.path())).exec() if url.scheme()=='doc' else QDesktopServices.openUrl(url))
    return view

class DocDialog(QDialog):
    """Rendered guide document (Markdown in 문서/) with 원문 보기·편집, ✎ open in the chosen editor, live reload."""
    def __init__(self,owner,name,title=None,intro=False,size=(800,820)):
        super().__init__(owner);self.owner=owner;self.name=name;self.sources=[];self.stamps={};self.setWindowTitle(title or name);self.resize(*size);v=QVBoxLayout(self)
        bar=QHBoxLayout();v.addLayout(bar);head=QLabel(f'📄 {name}.md');head.setObjectName('muted');bar.addWidget(head);bar.addStretch()
        self.pick=QComboBox();self.pick.setVisible(False);self.pick.currentIndexChanged.connect(self.load_raw);bar.addWidget(self.pick)
        self.raw_btn=QToolButton();self.raw_btn.setText('원문 (마크다운)');self.raw_btn.setCheckable(True);self.raw_btn.toggled.connect(self.toggle_raw);bar.addWidget(self.raw_btn)
        self.edit_btn=QToolButton();self.edit_btn.setText('✎ 편집');self.edit_btn.setToolTip('연결 프로그램으로 열기 (설정 → 문서·업데이트)');self.edit_btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);bar.addWidget(self.edit_btn)
        self.stack=QStackedWidget();v.addWidget(self.stack,1)
        self.view=QTextBrowser();self.view.setOpenLinks(False);self.view.anchorClicked.connect(self.follow);self.stack.addWidget(self.view)
        raw=QWidget();rv=QVBoxLayout(raw);rv.setContentsMargins(0,0,0,0);self.editor=QPlainTextEdit();self.editor.setFont(QFont('Consolas',10));rv.addWidget(self.editor)
        row=QHBoxLayout();rv.addLayout(row);row.addStretch();b=QPushButton('원문 저장');b.clicked.connect(self.save_raw);row.addWidget(b);self.stack.addWidget(raw)
        row=QHBoxLayout();v.addLayout(row)
        self.hide_next=QCheckBox('다시 표시하지 않기');self.hide_next.setVisible(intro);row.addWidget(self.hide_next);row.addStretch()
        b=QPushButton('확인' if intro else '닫기');b.setDefault(True);b.clicked.connect(self.accept);row.addWidget(b)
        self.timer=QTimer(self);self.timer.timeout.connect(self.watch);self.timer.start(1000)
        self.reload()
    def prefs(self):return getattr(self.owner,'prefs',{}) or {}
    def reload(self):
        html,self.sources=render(self.name);bar=self.view.verticalScrollBar();pos=bar.value();self.view.setHtml(html);bar.setValue(pos)
        self.stamps={p:p.stat().st_mtime for p in self.sources if p.exists()}
        menu=QMenu(self)
        for p in self.sources or [doc_path(self.name)]:menu.addAction(f'{p.stem}.md 편집',lambda p=p:open_in_editor(p,self.prefs()))
        menu.addSeparator();menu.addAction('문서 폴더 열기',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(doc_dir()))))
        menu.addAction('편집 프로그램 설정…',lambda:self.owner.settings('문서·업데이트') if hasattr(self.owner,'settings') else None);self.edit_btn.setMenu(menu)
        current=self.pick.currentText();self.pick.blockSignals(True);self.pick.clear();self.pick.addItems([p.stem for p in self.sources]);self.pick.blockSignals(False)
        if current in [p.stem for p in self.sources]:self.pick.setCurrentText(current)
    def watch(self):
        """Re-render when a source file was saved elsewhere (Obsidian, Notepad…)."""
        if self.raw_btn.isChecked():return
        if any(p.exists() and p.stat().st_mtime!=t for p,t in self.stamps.items()):self.reload()
    def toggle_raw(self,on):
        self.pick.setVisible(on);self.stack.setCurrentIndex(1 if on else 0)
        if on:self.load_raw()
        else:self.reload()
    def raw_path(self):return doc_path(self.pick.currentText()) if self.pick.currentText() else doc_path(self.name)
    def load_raw(self,*args):
        p=self.raw_path();self.editor.setPlainText(p.read_text(encoding='utf-8') if p.exists() else '')
    def save_raw(self):
        try:self.raw_path().write_text(self.editor.toPlainText(),encoding='utf-8')
        except OSError as e:return QMessageBox.warning(self,'저장 실패',str(e))
        self.raw_btn.setChecked(False)
    def follow(self,url):
        if url.scheme()=='doc':DocDialog(self.owner,doc_target(url.path()),size=(820,860)).exec()
        else:QDesktopServices.openUrl(url)

class StartDialog(QDialog):
    """Pick the user, then 직급·호봉; the monthly pay follows the salary table."""
    def __init__(self,p,current,skip,parent):
        super().__init__(parent);self.p=p;self.setWindowTitle('사용자 선택');self.resize(460,300);v=QVBoxLayout(self);f=QFormLayout();v.addLayout(f)
        self.user=QComboBox();self.user.addItems([f'{x["name"]} · {x["team"]}' for x in p['people']]);f.addRow('사용자',self.user)
        self.grade=QComboBox();self.grade.addItems(list(p['rates']));f.addRow('직급',self.grade)
        self.step=QSpinBox();self.step.setSpecialValueText('직접입력');self.step.setSuffix('호봉');f.addRow('호봉',self.step)
        self.pay=QLabel();self.pay.setObjectName('regular');f.addRow('월 봉급',self.pay)
        self.skip=QCheckBox('다음번에 다시 선택하지 않기');self.skip.setChecked(skip);v.addWidget(self.skip)
        note=QLabel('직급·호봉은 연가보상비 계산에 쓰입니다. 나중에 오른쪽 위 메뉴(☰) → 사용자·호봉에서 다시 바꿀 수 있습니다.');note.setObjectName('muted');note.setWordWrap(True);v.addWidget(note);buttons(self,v)
        self.user.currentIndexChanged.connect(self.load_user);self.grade.currentTextChanged.connect(self.update_pay);self.step.valueChanged.connect(self.update_pay)
        self.user.setCurrentIndex(current);self.load_user()
    def load_user(self):
        x=self.p['people'][self.user.currentIndex()];self.grade.blockSignals(True);self.grade.setCurrentText(x['grade']);self.grade.blockSignals(False)
        self.update_pay();self.step.setValue(int(x.get('step') or 0))
    def update_pay(self):
        table=salary_table(self.p)[1].get(self.grade.currentText(),[]);self.step.setRange(0,len(table))
        salary,basis=salary_of(dict(self.p['people'][self.user.currentIndex()],grade=self.grade.currentText(),step=self.step.value()),self.p)
        self.pay.setText(f'{salary:,.0f}원 ({basis})' if salary else '봉급표에 없는 직급입니다. 설정에서 본봉을 직접 입력하세요.' if not table else '호봉을 선택하세요')
    def choice(self):return self.user.currentIndex(),self.grade.currentText(),self.step.value(),self.skip.isChecked()

DETAIL_LINK=' &nbsp;<a href="detail" style="text-decoration:none;color:#0078d4;font-size:13px;font-weight:400">ⓘ 계산 근거</a>'

class HtmlDialog(QDialog):
    def __init__(self,parent,title,html,size=(780,760)):
        super().__init__(parent);self.setWindowTitle(title);self.resize(*size);v=QVBoxLayout(self)
        v.addWidget(html_view(html,self))
        b=QDialogButtonBox(QDialogButtonBox.StandardButton.Close);b.button(QDialogButtonBox.StandardButton.Close).setText('닫기');b.rejected.connect(self.reject);v.addWidget(b)

def won(x):return f'{x:,.0f}원'

class Toast(QFrame):
    """Brief top-right overlay after each edit: net gain/loss of that edit. Clicking it opens the calculation basis."""
    def __init__(self,parent):
        super().__init__(parent);self.setObjectName('toast');self.setCursor(Qt.CursorShape.PointingHandCursor);self.entry=None;self.on_click=None
        v=QVBoxLayout(self);v.setContentsMargins(16,10,16,10);self.text=QLabel();self.text.setTextFormat(Qt.TextFormat.RichText);v.addWidget(self.text)
        shadow=QGraphicsDropShadowEffect(self);shadow.setBlurRadius(24);shadow.setOffset(0,4);shadow.setColor(QColor(0,0,0,60));self.setGraphicsEffect(shadow)
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.hide);self.hide()
    def popup(self,entry,html,ms=6000):
        self.entry=entry;self.text.setText(html);self.adjustSize();p=self.parentWidget()
        self.move(p.width()-self.width()-24,64);self.show();self.raise_();self.timer.start(ms)
    def mousePressEvent(self,e):
        self.hide()
        if self.on_click and self.entry:self.on_click(self.entry)

class HistoryDialog(QDialog):
    """This session's edits with their net effect; double-click (or the button) shows the calculation basis."""
    def __init__(self,owner):
        super().__init__(owner);self.owner=owner;self.setWindowTitle('변경 히스토리 (이번 실행)');self.resize(900,480);v=QVBoxLayout(self)
        rows=list(reversed(owner.history));self.rows=rows
        t=QTableWidget(len(rows),7);t.setHorizontalHeaderLabels(['시각','사용자','월','변경','수당 차이','연가보상비','손익']);t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents);t.horizontalHeader().setStretchLastSection(True)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        for i,e in enumerate(rows):
            cv=e['cv']
            for j,val in enumerate([e['time'],e['name'],f'{e["y"]}-{e["m"]:02}',e['label'],f'{cv["gain"]:+,}원',f'{(-cv["comp"]) or 0:+,.0f}원',f'{cv["net"]:+,.0f}원']):
                item=QTableWidgetItem(val);t.setItem(i,j,item)
                if j==6:item.setForeground(QColor('#107c41' if cv['net']>0 else '#c23b46' if cv['net']<0 else '#5b6676'))
        t.cellDoubleClicked.connect(lambda r,c:owner.explain_change(rows[r]));v.addWidget(t);self.table=t
        note=QLabel('수정할 때마다 그 수정 직전과 비교한 손익입니다 (수당 차이 − 늘어난 연가일수 × 연가보상비). 프로그램을 끄면 비워집니다.' if rows else '아직 이번 실행에서 수정한 내용이 없습니다.');note.setObjectName('muted');note.setWordWrap(True);v.addWidget(note)
        row=QHBoxLayout();v.addLayout(row);row.addStretch()
        b=QPushButton('계산 근거 보기');b.clicked.connect(lambda:owner.explain_change(rows[t.currentRow()]) if rows and t.currentRow()>=0 else None);row.addWidget(b)
        b=QPushButton('닫기');b.clicked.connect(self.accept);row.addWidget(b)

class Main(QMainWindow):
    def __init__(self,project=None):
        super().__init__();self.resize(1200,900);self.p=project or demo_project();self.holidays={};self.loading=False
        self.prefs=load_prefs();self.refcache=(None,None);self.path=None;self.saved=snapshot(self.p)
        last=self.prefs.get('last_file')
        if last and os.path.exists(last):
            try:self.saved=snapshot(load_project(last));self.path=last
            except Exception:pass
        root=QWidget();self.setCentralWidget(root);v=QVBoxLayout(root);v.setContentsMargins(24,18,24,18);v.setSpacing(12)
        self.history=[]
        head=QHBoxLayout();v.addLayout(head)
        self.user=QComboBox();self.user.setMinimumWidth(200);head.addWidget(self.user);self.user.currentIndexChanged.connect(self.refresh)
        self.summary=QLabel();self.summary.setObjectName('summary');self.summary.setTextFormat(Qt.TextFormat.RichText);head.addWidget(self.summary);head.addStretch()
        menu=QMenu(self)
        for item in [('엑셀 가져오기',self.import_file),None,('저장',self.save,QKeySequence.StandardKey.Save),('다른 이름으로 저장',self.save_as),('불러오기',self.open_project),None,
                     ('변경 히스토리',lambda:HistoryDialog(self).exec()),('업데이트 확인',lambda:self.check_update(manual=True)),('연간 CSV 내보내기',self.export_csv),('가져오기 확인사항',self.show_warnings),None,
                     ('사용자·호봉',self.choose_user),('설정',lambda:self.settings())]:
            if item is None:menu.addSeparator();continue
            act=QAction(item[0],self);act.triggered.connect(item[1]);menu.addAction(act)
            if len(item)>2:act.setShortcut(QKeySequence(item[2]));self.addAction(act)
        b=QToolButton();b.setText('☰');b.setObjectName('help');b.setToolTip('메뉴');b.setMenu(menu);b.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup);head.addWidget(b)
        b=QToolButton();b.setText('?');b.setObjectName('help');b.setToolTip('산식과 법적 근거 (왜 시간을 공제하는지)');b.clicked.connect(lambda:DocDialog(self,'도움말','산식과 법적 근거').exec());head.addWidget(b)
        self.tabs=QTabWidget();v.addWidget(self.tabs,1);page=QWidget();row=QHBoxLayout(page);self.cal=Calendar(self);row.addWidget(self.cal,3)
        sidew=QWidget();sidew.setObjectName('side');side=QVBoxLayout(sidew);side.setSpacing(10);scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setWidget(sidew);row.addWidget(scroll,2)
        self.details=QLabel();self.details.setTextFormat(Qt.TextFormat.RichText);self.details.setWordWrap(True);side.addWidget(self.details)
        self.day=QLabel();self.day.setObjectName('day');self.day.setTextFormat(Qt.TextFormat.RichText);self.day.setWordWrap(True);side.addWidget(self.day);self.day.linkActivated.connect(lambda _:self.show_detail('leave'))
        tools=QHBoxLayout();side.addLayout(tools)
        for label,fn in [('날짜 편집',self.edit_day),('수정 취소',self.reset_day),('월 조정값',self.month_adjust)]:
            b=QPushButton(label);b.clicked.connect(fn);tools.addWidget(b)
        self.probe=spin(8,240);f=QFormLayout();f.addRow('추가 교육/근무 가정 (시간)',self.probe);side.addLayout(f);self.probe.valueChanged.connect(self.refresh)
        self.benefit=QLabel();self.benefit.setObjectName('muted');self.benefit.setWordWrap(True);side.addWidget(self.benefit)
        self.cycleinfo=QLabel();self.cycleinfo.setObjectName('day');self.cycleinfo.setTextFormat(Qt.TextFormat.RichText);self.cycleinfo.setWordWrap(True);side.addWidget(self.cycleinfo);self.cycleinfo.linkActivated.connect(lambda _:self.show_detail('cycle'))
        card=QFrame();card.setObjectName('refcard');g=QGridLayout(card);g.setContentsMargins(10,10,6,10);side.addWidget(card)
        self.refinfo=QLabel();self.refinfo.setTextFormat(Qt.TextFormat.RichText);self.refinfo.setWordWrap(True);g.addWidget(self.refinfo,0,0);self.refinfo.linkActivated.connect(lambda _:self.show_detail('ref'));g.setColumnStretch(0,1)
        b=QToolButton();b.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon));b.setToolTip('비교할 저장파일 선택');b.setAutoRaise(True);b.clicked.connect(self.choose_ref);g.addWidget(b,0,1,Qt.AlignmentFlag.AlignTop|Qt.AlignmentFlag.AlignRight)
        side.addStretch();self.tabs.addTab(page,'월간 달력')
        self.annual=QTableWidget(12,9);self.annual.setHorizontalHeaderLabels(['월','초과 인정시간','예상 수당','추가시간 반영 후','추가 실익','연가 손익 (보상비 차감)','주야비비 대비 손익','저장파일 대비 손익','근무표 출처']);self.annual.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch);self.annual.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers);self.annual.cellDoubleClicked.connect(lambda r,c:self.cal.setCurrentPage(self.cal.yearShown(),r+1));self.tabs.addTab(self.annual,'월별 교육·근무·연가 실익')
        self.notice=QLabel();self.notice.setObjectName('muted');self.notice.setWordWrap(True);v.addWidget(self.notice)
        self.cal.currentPageChanged.connect(self.refresh);self.cal.selectionChanged.connect(self.refresh_details);self.cal.activated.connect(self.edit_day)
        self.toast=Toast(root);self.toast.on_click=self.explain_change
        self.repopulate();ym=self.p.get('import_month',dt.date.today().strftime('%Y-%m'));y,m=map(int,ym.split('-'));self.cal.setSelectedDate(QDate(y,m,1));self.refresh()
    def person(self):return self.p['people'][max(0,self.user.currentIndex())]
    def repopulate(self):
        i=self.user.currentIndex();self.user.blockSignals(True);self.user.clear()
        for x in self.p['people']:self.user.addItem(f'{x["name"]} · {x["grade"]} · {x["team"]}')
        self.user.setCurrentIndex(max(0,min(i,len(self.p['people'])-1)));self.user.blockSignals(False)
    def refresh(self,*args):
        if not self.p['people']:return
        y,m=self.cal.yearShown(),self.cal.monthShown();person=self.person();pid=person['id'];self.holidays=holiday_map(self.p,y)
        r=calculate(self.p,pid,y,m);n=calculate(self.p,pid,y,m,True,self.probe.value());invalid=bool(r['unknown'])
        color='#c23b46' if r['signed']<0 else '#107c41'
        self.summary.setText(f'예상 수당 <b>{r["pay"]:,}원</b> &nbsp;·&nbsp; 차감 후 초과 <b style="color:{color}">{r["signed"]:+g}시간</b>' if not invalid else '<b style="color:#c23b46">미등록 근무코드가 있습니다 — 날짜를 수정하세요</b>')
        self.benefit.setText(f'→ 수당 {n["pay"]-r["pay"]:+,}원'+(f' · 초과가 {r["signed"]:+g}시간이라 {-r["signed"]:g}시간을 먼저 채워야 시간외가 늘어납니다' if r['signed']<0 else ''))
        ref=self.ref_project()
        for month in range(1,13):
            a=calculate(self.p,pid,y,month);b=calculate(self.p,pid,y,month,True,self.probe.value());source='엑셀' if has_roster(self.p,pid,y,month) else '4교대 자동 생성'
            lv=month_leave_value(self.p,pid,y,month);cc=change_value(self.p,pid,y,month,'cycle');cv=file_change_value(self.p,pid,y,month,ref) if ref else None
            vals=[f'{month}월',f'{a["signed"]:+g} h',f'{a["pay"]:,}원',f'{b["pay"]:,}원',f'{b["pay"]-a["pay"]:+,}원',f'{lv["net"]:+,.0f}원 (연가 {lv["days"]:g}일)' if lv else '',f'{cc["net"]:+,.0f}원 ({len(cc["changed"])}일)' if cc['changed'] else '',f'{cv["net"]:+,.0f}원 ({len(cv["changed"])}일)' if cv and (cv['changed'] or cv['net']) else '',source]
            for col,value in enumerate(vals):self.annual.setItem(month-1,col,QTableWidgetItem(value))
        notes=([f'수당 단가는 {self.p["rate_year"]}년 기준입니다 (설정에서 변경).'] if y!=self.p['rate_year'] else [])+self.p.get('warnings',[])[:2];self.notice.setText('\n'.join(notes))
        self.cal.regular.setText(f'정규근무 {r["base_days"]}일 · {r["base_days"]*8}시간');self.cycleinfo.setText(self.cycle_html(person,y,m));self.refinfo.setText(self.ref_html(person,y,m))
        self.setWindowTitle(f'교대근무 수당 플래너 v{VERSION} — '+(Path(self.path).stem if self.path else '저장 안 된 작업')+(' *' if self.dirty() else ''))
        self.cal.updateCells();self.refresh_details()
    def diff_html(self,head,cv,person,empty):
        """Shared body of the comparison cards: changed dates, overtime, pay parts, forgone 연가보상비, net."""
        muted='color:#8a94a3'
        if not cv['changed'] and not cv['gain'] and not cv['leave_days']:return head+f'<div style="color:#5b6676">{empty}</div>'
        b,a=cv['before'],cv['after']
        items=[f'{d.day}일 {escape(o)}→{escape(n)}' if o!=n else f'{d.day}일 {escape(n)} 수정' for d,o,n in cv['changed']]
        items=items[:8]+([f'외 {len(items)-8}일'] if len(items)>8 else []) or ['날짜 변경 없음 (설정·조정값 차이)']
        parts=[f'{k} {a[key]-b[key]:+,}' for k,key in [('시간외','overtime'),('야간','night_pay'),('휴일','holiday_pay')] if a[key]!=b[key]]
        out=head+f'<div style="color:#5b6676">{" · ".join(items)}</div>'
        out+=f'<div>초과 {b["signed"]:+g} → {a["signed"]:+g}시간</div><div>수당 <b>{cv["gain"]:+,}원</b> <span style="{muted}">({" · ".join(parts) or "변동 없음"})</span></div>'
        if cv['leave_days']:
            if not person.get('leave_comp',True):out+=f'<div>연가 {cv["leave_days"]:+g}일 <span style="{muted}">(연가보상 대상 아님 → 0원)</span></div>'
            elif not salary_of(person,self.p)[0]:out+=f'<div>연가 {cv["leave_days"]:+g}일 <span style="{muted}">(호봉·본봉을 넣으면 보상비 반영)</span></div>'
            else:out+=f'<div>연가 {cv["leave_days"]:+g}일 → 연가보상비 <b>{-cv["comp"]:+,.0f}원</b></div>'
        color='#107c41' if cv['net']>0 else '#c23b46' if cv['net']<0 else '#5b6676';verdict='이득' if cv['net']>0 else '손해' if cv['net']<0 else '차이 없음'
        return out+f'<div style="font-size:15px;font-weight:700;color:{color}">= {cv["net"]:+,.0f}원 {verdict}{DETAIL_LINK}</div>'
    def cycle_html(self,person,y,m):
        head='<div style="font-weight:600">주야비비 원형 대비 <span style="font-weight:400;color:#5b6676">· 연가·근무교환을 전혀 안 했을 때와 비교</span></div>'
        return self.diff_html(head,change_value(self.p,person['id'],y,m,'cycle'),person,'주야비비와 다른 날짜가 없습니다.')
    def ref_html(self,person,y,m):
        """Current plan vs. a saved scenario file (default: the file last saved or opened; folder icon picks another)."""
        path=self.ref_path();muted=lambda t:f'<div style="color:#5b6676">{t}</div>'
        if not path:return '<div style="font-weight:600">저장파일 대비 변경</div>'+muted('기준 저장파일이 없습니다. "다른 이름으로 저장"으로 파일을 만들거나 폴더 아이콘으로 선택하세요.')
        head=f'<div style="font-weight:600">「{escape(Path(path).stem)}」 대비 변경</div><div style="color:#8a94a3">저장 {dt.datetime.fromtimestamp(os.path.getmtime(path)):%Y-%m-%d %H:%M}'+(' · 현재 작업 파일' if path==self.path else '')+'</div>'
        ref=self.ref_project()
        if ref is None:return head+muted('파일을 읽을 수 없습니다. 폴더 아이콘으로 다시 선택하세요.')
        cv=file_change_value(self.p,person['id'],y,m,ref)
        if cv is None:return head+muted('이 파일에는 현재 사용자가 없습니다.')
        return self.diff_html(head,cv,person,'저장한 뒤 이 달에 바꾼 것이 없습니다.')
    def show_detail(self,kind):
        """Popup with the step-by-step basis of one result card, including the 연가보상비 side."""
        person=self.person();pid=person['id'];y,m=self.cal.yearShown(),self.cal.monthShown()
        if kind=='leave':
            d=self.cal.selectedDate().toPython();cv=leave_value(self.p,pid,d);y,m=d.year,d.month
            name,desc=f'{d.day}일 연가를 쓰지 않고 비웠을 때','그날을 연가 대신 0시간(비번)으로 두고 연가를 아껴 연가보상비로 받는 경우입니다.'
        elif kind=='cycle':
            cv=change_value(self.p,pid,y,m,'cycle');name,desc='주야비비 원형','연가·근무교환·엑셀 편성 변경을 전혀 하지 않고 주→야→비→비만 섰을 때입니다.'
        else:
            ref=self.ref_project();cv=file_change_value(self.p,pid,y,m,ref) if ref else None
            name,desc=f'저장파일 「{Path(self.ref_path()).stem}」' if ref else '','그 파일을 저장했을 때의 달력과 설정입니다.'
        if cv:HtmlDialog(self,'손익 계산 근거',explain_html(self.p,pid,y,m,cv,name,desc),(860,820)).exec()
    def ref_path(self):
        return next((x for x in [self.prefs.get('compare_file'),self.path] if x and os.path.exists(x)),None)
    def ref_project(self):
        path=self.ref_path()
        if not path:return None
        key=(path,os.path.getmtime(path))
        if self.refcache[0]!=key:
            try:self.refcache=(key,load_project(path))
            except Exception:self.refcache=(key,None)
        return self.refcache[1]
    def choose_ref(self):
        path,_=QFileDialog.getOpenFileName(self,'비교할 저장파일 선택',os.path.dirname(self.ref_path() or self.path or ''),'JSON (*.json)')
        if path:self.prefs['compare_file']=path;self.save_prefs();self.refresh()
    def hours_html(self,r,y,m):
        """Month total vs. deduction, the comparison that decides overtime pay."""
        num='font-size:20px;font-weight:700';lab='color:#5b6676;padding-right:10px';color='#c23b46' if r['signed']<0 else '#107c41'
        deduct=r['base_days']*8+r['holiday']*8-r['basic']
        formula=f'({r["base_days"]}일 × 8시간)'+(f' + <sup>휴일</sup>({r["holiday"]:g}일 × 8시간)' if r['holiday'] else '')+(f' − 가산 {r["basic"]:g}시간' if r['basic'] else '')
        floor=f' <span style="color:#8a94a3;font-size:11px">({r["raw"]:g} 절사)</span>' if r['raw']!=r['total'] else ''
        rows=[('총근무',f'<span style="{num}">{r["total"]:g}</span>시간',floor),('공제',f'<span style="{num}">{deduct:g}</span>시간',f'= {formula}'),('초과',f'<span style="{num};color:{color}">{r["signed"]:+g}</span>시간','시간외수당 0원 (음수)' if r['signed']<0 else '')]
        table=''.join(f'<tr><td valign="middle" style="{lab}">{a}</td><td valign="middle" align="right" style="padding-right:10px">{b}</td><td valign="middle">{c}</td></tr>' for a,b,c in rows)
        return (f'<div style="font-weight:600;margin-bottom:4px">{y}년 {m}월 근무시간</div><table cellspacing="0" cellpadding="2">{table}</table>'
                f'<div style="color:#5b6676;margin-top:6px">시간외 {won(r["overtime"])} · 야간 {r["night"]:g}h {won(r["night_pay"])} · 휴일 {r["holiday"]:g}일 {won(r["holiday_pay"])}</div>')
    def leave_html(self,person,d,code):
        """Annual-leave day only: pay from the leave credit vs. 연가보상비 forgone by using it."""
        if code in LEAVE and leave_days(self.p,code)<=0:return '<div style="color:#5b6676">특별휴가·공가·병가는 연가일수를 차감하지 않아 연가보상비와 비교하지 않습니다.</div>'
        lv=leave_value(self.p,person['id'],d)
        if lv is None:return ''
        out=f'<div style="font-weight:600;margin-top:6px">연가 쓸 때 vs 안 쓰고 비울 때 <span style="font-weight:400;color:#5b6676">(연가 {lv["days"]:g}일)</span></div>'
        out+=f'<div>연가로 받는 수당 <b>{lv["gain"]:+,}원</b> <span style="color:#8a94a3">(초과 {lv["without"]:+g} → {lv["signed"]:+g}h)</span></div>'
        if not person.get('leave_comp',True):return out+'<div style="color:#5b6676">연가보상 대상 아님(N) → 포기하는 보상비 0원</div>'
        if not salary_of(person,self.p)[0]:return out+'<div style="color:#5b6676">설정 → 사용자에서 호봉(또는 본봉)을 넣으면 연가보상비와 비교합니다.</div>'
        color='#107c41' if lv['net']>0 else '#c23b46';verdict='연가 쓰는 게 유리' if lv['net']>0 else '연가 안 쓰는 게 유리' if lv['net']<0 else '차이 없음'
        out+=f'<div>포기하는 연가보상비 <b>−{won(lv["comp"])}</b></div><div style="font-size:15px;font-weight:700;color:{color}">= {lv["net"]:+,.0f}원 → {verdict}{DETAIL_LINK}</div>'
        if math.isfinite(lv['need_hours']):out+=f'<div style="color:#8a94a3">손익분기: 연가로 생긴 {lv["hours"]:g}시간 중 {lv["need_hours"]:.1f}시간 이상이 시간외로 지급돼야 이득</div>'
        return out
    def refresh_details(self):
        y,m=self.cal.yearShown(),self.cal.monthShown();person=self.person();d=self.cal.selectedDate().toPython();e=event(self.p,person,d)
        self.details.setText(self.hours_html(calculate(self.p,person['id'],y,m),y,m))
        tags=[t for t in [self.holidays.get(str(d),''),'수정됨' if str(d) in self.p['overrides'].get(person['id'],{}) else '',escape(e.get('memo',''))] if t]
        text=f'<div style="font-weight:600">{d.month}월 {d.day}일 ({"월화수목금토일"[d.weekday()]}) · {escape(e["code"])}</div>'+(f'<div style="color:#5b6676">{" · ".join(tags)}</div>' if tags else '')
        self.day.setText(text+self.leave_html(person,d,e['code']))
    def show_warnings(self):
        QMessageBox.information(self,'가져오기 확인사항','\n'.join(self.p.get('warnings',[])) or '확인사항 없음')
    def month_adjust(self):
        key=f'{self.cal.yearShown()}-{self.cal.monthShown():02}';pid=self.person()['id'];old=self.p['adjustments'].get(pid,{}).get(key,{})
        dlg=QDialog(self);dlg.setWindowTitle(key+' 월 조정값');v=QVBoxLayout(dlg);f=QFormLayout();v.addLayout(f);fields={}
        for k,label in [('hours','총시간 추가/차감'),('night','야간수당시간 추가/차감'),('basic','기본시간 가산')]:
            box=spin(0,10000);box.setMinimum(-10000);box.setValue(old.get(k,0));fields[k]=box;f.addRow(label,box)
        v.addWidget(QLabel('엑셀에서 가져온 월 보정값을 포함합니다. 날짜별 추가시간과 중복 입력하지 마세요.'));buttons(dlg,v)
        if dlg.exec():
            before=copy.deepcopy(self.p);self.p['adjustments'].setdefault(pid,{})[key]={k:b.value() for k,b in fields.items()};self.persist();self.refresh()
            self.record_change(f'{key} 월 조정값',before,pid,self.cal.yearShown(),self.cal.monthShown())
    def record_change(self,label,before,pid,y,m):
        """Net effect of one edit (this month, this person) versus the moment just before it; shown as a toast and kept in 변경 히스토리."""
        bperson=next((x for x in before['people'] if x['id']==pid),None)
        if bperson is None or not any(x['id']==pid for x in self.p['people']):return
        after=copy.deepcopy(self.p);cv=diff_value(after,pid,y,m,before,bperson,True,True);name=next(x['name'] for x in after['people'] if x['id']==pid)
        entry=dict(time=dt.datetime.now().strftime('%H:%M:%S'),pid=pid,name=name,y=y,m=m,label=label,cv=cv,after=after);self.history.append(entry)
        net=cv['net'];color='#107c41' if net>0 else '#c23b46' if net<0 else '#5b6676';word='실익' if net>0 else '손해' if net<0 else '변동 없음'
        self.toast.popup(entry,f'<div style="font-size:20px;font-weight:700;color:{color}">{net:+,.0f}원 {word}</div><div style="color:#5b6676">{escape(name)} · {m}월 · {escape(label)}</div>'+self.hours_line(cv)+'<div style="color:#0078d4">눌러서 계산 근거 보기</div>')
    def hours_line(self,cv):
        """Toast line when the negative overtime balance absorbed a big part of the change in worked hours."""
        h=hours_effect(cv)
        if not h or abs(h['absorbed'])<1:return ''
        if h['hours']<0:
            per=f' · 줄인 1시간당 {-cv["net"]/-h["hours"]:,.0f}원' if cv['net']<0 else ''
            text=f'근무 {-h["hours"]:g}h 감소, 시간외 반영은 {-h["paid"]:g}h뿐{per}<br>초과가 마이너스라 근무를 줄인 것에 비해 손해가 작습니다'
        else:
            text=f'근무 {h["hours"]:g}h 증가, 시간외 반영은 {h["paid"]:g}h뿐<br>늘린 근무가 초과 마이너스를 메우는 데 쓰였습니다'
        return f'<div style="color:#1f5f99">{text}</div>'
    def explain_change(self,entry):
        HtmlDialog(self,'수정 손익 계산 근거',explain_html(entry['after'],entry['pid'],entry['y'],entry['m'],entry['cv'],f'수정 전 ({entry["label"]})','이번 수정 바로 직전의 달력과 설정입니다.'),(860,820)).exec()
    def persist(self):
        try:save_project(self.p,STORE);self.statusBar().showMessage('사용자별 변경사항 자동 저장 완료',5000)
        except Exception as e:QMessageBox.critical(self,'자동 저장 실패',str(e))
    def edit_day(self,*args):
        d=self.cal.selectedDate().toPython();old=event(self.p,self.person(),d);dlg=DayDialog(old,d,self)
        if dlg.exec():
            before=copy.deepcopy(self.p);pid=self.person()['id'];new=dlg.result_event();self.p['overrides'].setdefault(pid,{})[str(d)]=new;self.persist();self.refresh()
            self.record_change(f'{d.month}/{d.day} {old["code"]}→{new["code"]}' if old['code']!=new['code'] else f'{d.month}/{d.day} {new["code"]} 수정',before,pid,d.year,d.month)
    def reset_day(self):
        d=self.cal.selectedDate().toPython();pid=self.person()['id']
        if str(d) not in self.p['overrides'].get(pid,{}):return
        before=copy.deepcopy(self.p);old=event(self.p,self.person(),d)['code'];self.p['overrides'][pid].pop(str(d));self.persist();self.refresh()
        self.record_change(f'{d.month}/{d.day} {old} 수정 취소',before,pid,d.year,d.month)
    def settings(self,tab=None):
        dlg=Settings(self.p,self,tab)
        if dlg.exec():
            before=self.p;pid=self.person()['id'];self.p=dlg.p;self.repopulate();self.persist();self.refresh()
            if snapshot(before)!=snapshot(self.p):self.record_change('설정 변경',before,pid,self.cal.yearShown(),self.cal.monthShown())
    def import_file(self):
        path,_=QFileDialog.getOpenFileName(self,'근무 편성표 또는 수당 산출표 선택','','Excel (*.xlsx)')
        if not path:return
        try:
            p=import_excel(path)
            if self.p.get('source'):
                # Match identity by name and grade; preserve other months and edits.
                for person in p['people']:
                    old=next((x for x in self.p['people'] if x['name']==person['name'] and x['grade']==person['grade']),None)
                    if old:
                        oldid,newid=old['id'],person['id'];merged=copy.deepcopy(self.p['baseline'].get(oldid,{}));merged.update(p['baseline'].get(newid,{}));p['baseline'][newid]=merged;p['overrides'][newid]=copy.deepcopy(self.p['overrides'].get(oldid,{}));adj=copy.deepcopy(self.p['adjustments'].get(oldid,{}));adj.update(p['adjustments'].get(newid,{}));p['adjustments'][newid]=adj;person.update(salary=old.get('salary',0),step=old.get('step',0),leave_comp=old.get('leave_comp',True))
                for k in ['rates','rate_year','round_hours','holidays_add','holidays_remove','work_hours','leave_hours','leave_days','leave_comp']:p[k]=copy.deepcopy(self.p.get(k,p.get(k)))
            self.p=p;self.repopulate();y,m=map(int,p['import_month'].split('-'));self.cal.setSelectedDate(QDate(y,m,1));self.persist();self.refresh()
            QMessageBox.information(self,'가져오기 완료',f'{len(p["people"])}명 / {p["import_month"]}\n기존 사용자 수정안은 유지됩니다.\n\n'+'\n'.join(p['warnings']))
        except Exception as e:QMessageBox.critical(self,'가져오기 실패',str(e))
    def dirty(self):return snapshot(self.p)!=self.saved
    def save_prefs(self):
        try:save_project(self.prefs,prefs_path())
        except Exception:pass
    def write(self,path):
        try:save_project(self.p,path)
        except Exception as e:QMessageBox.critical(self,'저장 실패',str(e));return False
        self.path=path;self.saved=snapshot(self.p);self.prefs['last_file']=path;self.save_prefs();self.refresh();self.statusBar().showMessage('저장 완료: '+path,5000);return True
    def save(self):return self.write(self.path) if self.path else self.save_as()
    def save_as(self):
        name=f'{dt.date.today():%m-%d} {self.person()["name"]} 수정.json'
        path,_=QFileDialog.getSaveFileName(self,'전체 사용자 시나리오 저장',os.path.join(os.path.dirname(self.path),name) if self.path else name,'JSON (*.json)')
        return bool(path) and self.write(path)
    def ask_save(self):
        """'save' / 'discard' / 'cancel' for unsaved changes."""
        if not self.dirty():return 'discard'
        box=QMessageBox(self);box.setIcon(QMessageBox.Icon.Question);box.setWindowTitle('저장 확인');box.setText('저장하지 않은 변경이 있습니다. 저장하시겠습니까?')
        save=box.addButton('저장',QMessageBox.ButtonRole.AcceptRole);discard=box.addButton('저장 안 함',QMessageBox.ButtonRole.DestructiveRole);box.addButton('취소',QMessageBox.ButtonRole.RejectRole);box.exec()
        return 'save' if box.clickedButton()==save else 'discard' if box.clickedButton()==discard else 'cancel'
    def closeEvent(self,e):
        answer=getattr(self,'close_answer',None) or self.ask_save()
        if answer=='save' and not self.save():answer='cancel'
        if answer=='cancel':return e.ignore()
        if answer=='discard' and self.dirty():
            try:save_project(self.saved,STORE)  # next start resumes from the last saved state, not the discarded edits
            except Exception:pass
        if getattr(self,'pending_update',None):
            try:updater.start_finish(self.pending_update)
            except OSError as err:QMessageBox.warning(self,'업데이트','새 버전을 적용하지 못했습니다: '+str(err))
        e.accept()
    def open_project(self):
        answer=self.ask_save()
        if answer=='cancel' or answer=='save' and not self.save():return
        path,_=QFileDialog.getOpenFileName(self,'시나리오 불러오기',os.path.dirname(self.path or ''),'JSON (*.json)')
        if path:
            try:self.p=load_project(path);self.path=path;self.saved=snapshot(self.p);self.prefs['last_file']=path;self.save_prefs();self.repopulate();self.persist();self.refresh()
            except Exception as e:QMessageBox.critical(self,'열기 실패',str(e))
    def choose_user(self):
        dlg=StartDialog(self.p,max(0,self.user.currentIndex()),self.prefs.get('skip_user_dialog',False),self)
        if not dlg.exec():return
        i,grade,step,skip=dlg.choice();person=self.p['people'][i]
        before=copy.deepcopy(self.p) if (person['grade'],int(person.get('step') or 0))!=(grade,step) else None
        if before:person.update(grade=grade,step=step);self.persist()
        self.prefs.update(default_user={'id':person['id'],'name':person['name']},skip_user_dialog=skip);self.save_prefs()
        self.repopulate();self.user.setCurrentIndex(i);self.refresh()
        if before:self.record_change('직급·호봉 변경',before,person['id'],self.cal.yearShown(),self.cal.monthShown())
    def check_salary_year(self,today=None):
        today=today or dt.date.today()
        if not salary_outdated(self.p,today):return
        box=QMessageBox(self);box.setIcon(QMessageBox.Icon.Warning);box.setWindowTitle('봉급표 확인');box.setText('연간 봉급표가 갱신되지 않았습니다. 확인해주세요.')
        box.setInformativeText(f'현재 봉급표: {salary_table(self.p)[0]}년 / 올해: {today.year}년\n\n· 설정 → 봉급표 탭에 인사혁신처 새 봉급표를 붙여넣고 연도를 바꾸거나\n· AI(바이브코딩)에게 pay_tables.py를 새 연도 표로 갱신해 달라고 요청하세요.\n\n연가보상비 계산에 쓰입니다. 수당 단가(설정 → 수당 단가)도 함께 확인하세요.')
        go=box.addButton('봉급표 설정 열기',QMessageBox.ButtonRole.AcceptRole);box.addButton('나중에',QMessageBox.ButtonRole.RejectRole);box.exec()
        if box.clickedButton()==go:self.settings('봉급표')
    def startup(self):
        """Intro notice (unless hidden), start user (unless '다시 선택하지 않기'), then the yearly salary-table check."""
        if not self.prefs.get('hide_intro'):
            dlg=DocDialog(self,'시작 안내','교대근무 수당 플래너 — 만든 이유',intro=True);dlg.exec()
            if dlg.hide_next.isChecked():self.prefs['hide_intro']=True;self.save_prefs()
        want=self.prefs.get('default_user') or {}
        i=next((k for k,x in enumerate(self.p['people']) if x['id']==want.get('id')),None)
        if i is None:i=next((k for k,x in enumerate(self.p['people']) if x['name']==want.get('name')),None)
        if self.prefs.get('skip_user_dialog') and i is not None:self.user.setCurrentIndex(i)
        else:
            if i is not None:self.user.setCurrentIndex(i)
            self.choose_user()
        self.check_salary_year()
        if self.prefs.get('update_check',True) and updater.mode()!='none':self.check_update(manual=False)
    def check_update(self,manual=False):
        """Look for a newer version in the background; manual=True also reports 'up to date' and errors."""
        mode=updater.mode()
        if mode=='none':
            if manual:QMessageBox.information(self,'업데이트',f'v{VERSION}\n소스 실행이지만 git 저장소가 아니어서 업데이트를 확인할 수 없습니다.')
            return
        box={}
        threading.Thread(target=lambda:box.update(r=updater.check() if mode=='exe' else updater.git_status()),daemon=True).start()
        if manual:self.statusBar().showMessage('업데이트 확인 중…')
        def poll():
            if 'r' not in box:return QTimer.singleShot(200,poll)
            self.statusBar().clearMessage();self.update_result(mode,box['r'],manual)
        QTimer.singleShot(200,poll)
    def update_result(self,mode,r,manual):
        if not r.get('ok'):
            if manual:QMessageBox.warning(self,'업데이트 확인',f'현재 v{VERSION}\n{r.get("error")}\n\n확인하지 못한 것이지 최신이라는 뜻은 아닙니다.')
            else:self.statusBar().showMessage('업데이트 확인 실패: '+(r.get('error') or ''),8000)
            return
        if mode=='git':
            if not r['behind']:
                if manual:QMessageBox.information(self,'업데이트',f'최신입니다 (v{VERSION}).')
                return
            text='\n'.join(r['incoming'])+('\n\n⚠️ 이 PC에서 고친 파일: '+', '.join(r['dirty'][:8]) if r['dirty'] else '')
            if QMessageBox.question(self,'업데이트',f'새 변경 {r["behind"]}개가 있습니다.\n\n{text}\n\n지금 받을까요? (git pull --ff-only)')!=QMessageBox.StandardButton.Yes:return
            res=updater.git_pull()
            if res['ok']:QMessageBox.information(self,'업데이트','받았습니다. 프로그램을 다시 켜면 반영됩니다.')
            else:QMessageBox.warning(self,'업데이트','받지 못했습니다:\n'+res['error'])
            return
        if not r['newer']:
            if manual:QMessageBox.information(self,'업데이트',f'최신입니다 (v{VERSION}).')
            return
        notes=(r['notes'] or '').strip()[:1500]
        if self.prefs.get('auto_update') and not manual:return self.auto_download(r)
        box=QMessageBox(self);box.setWindowTitle('업데이트');box.setText(f'새 버전 v{r["latest"]}이 있습니다 (현재 v{VERSION}).');box.setDetailedText(notes) if notes else None
        box.setInformativeText('지금 받아서 설치하면 프로그램이 다시 켜집니다. 저장하지 않은 변경은 먼저 저장 여부를 묻습니다.')
        now=box.addButton('지금 업데이트',QMessageBox.ButtonRole.AcceptRole);box.addButton('나중에',QMessageBox.ButtonRole.RejectRole);box.exec()
        if box.clickedButton()==now:self.install_update(r)
    def install_update(self,info):
        answer=self.ask_save()
        if answer=='cancel' or answer=='save' and not self.save():return
        dlg=QProgressDialog('새 버전을 받는 중…','취소',0,100,self);dlg.setWindowTitle('업데이트');dlg.setMinimumDuration(0);dlg.setAutoClose(False)
        def progress(done,total):
            dlg.setValue(int(done*100/total) if total else 0);QApplication.processEvents()
            if dlg.wasCanceled():raise RuntimeError('취소했습니다')
        try:staged=updater.download(info,progress)
        except Exception as e:dlg.close();return QMessageBox.warning(self,'업데이트','받지 못했습니다: '+str(e))
        dlg.close();self.pending_update=staged;self.close_answer='discard';self.close()  # already saved or discarded above
    def auto_download(self,info):
        """자동 업데이트: download quietly, then offer to restart now; otherwise it is applied when the program closes."""
        box={}
        def work():
            try:box['staged']=updater.download(info)
            except Exception as e:box['error']=str(e)
        threading.Thread(target=work,daemon=True).start()
        def poll():
            if not box:return QTimer.singleShot(500,poll)
            if 'error' in box:return self.statusBar().showMessage('자동 업데이트 실패: '+box['error'],10000)
            self.pending_update=box['staged']
            if QMessageBox.question(self,'업데이트',f'새 버전 v{info["latest"]}을 받았습니다. 지금 다시 시작해 적용할까요?\n(나중에 하면 프로그램을 끌 때 적용됩니다.)')==QMessageBox.StandardButton.Yes:self.close()
        QTimer.singleShot(500,poll)
    def export_csv(self):
        path,_=QFileDialog.getSaveFileName(self,'선택 사용자 연간 비교 저장',self.person()['name']+'_연간비교.csv','CSV (*.csv)')
        if path:
            try:
                with open(path,'w',encoding='utf-8-sig',newline='') as f:
                    w=csv.writer(f);n=self.annual.columnCount();w.writerow([self.annual.horizontalHeaderItem(c).text() for c in range(n)])
                    for r in range(12):w.writerow([self.annual.item(r,c).text() for c in range(n)])
            except Exception as e:QMessageBox.critical(self,'내보내기 실패',str(e))

STYLE='''QWidget {font-family: "Malgun Gothic", "Noto Sans CJK KR", sans-serif; font-size: 13px; color:#243247;} QMainWindow, QDialog {background:#f4f6fa;} QLabel#title {font-size:26px;font-weight:700;padding:4px;} QLabel#card {background:white;border:1px solid #e1e6ed;border-radius:10px;padding:15px;font-size:17px;font-weight:600;} QPushButton {background:#ffffff;border:1px solid #ced6e1;border-radius:6px;padding:9px 13px;} QPushButton:hover {background:#e8f2ff;border-color:#0078d4;} QComboBox,QLineEdit,QSpinBox,QDoubleSpinBox {background:white;border:1px solid #cbd5e1;border-radius:5px;padding:6px;} QTabWidget::pane {background:white;border:1px solid #e1e6ed;border-radius:8px;} QTabBar::tab {padding:12px 20px;} QTabBar::tab:selected {color:#0078d4;background:white;} QCalendarWidget QWidget#qt_calendar_navigationbar {background:#edf3fa;} QCalendarWidget QToolButton {color:#243247;padding:8px;} QWidget#side {background:white;} QLabel#regular {color:#0078d4;font-weight:600;padding-left:14px;} QLabel#day {background:#f7f9fc;border:1px solid #e1e6ed;border-radius:8px;padding:10px;} QFrame#refcard {background:#f7f9fc;border:1px solid #e1e6ed;border-radius:8px;} QLabel#muted {color:#5b6676;} QToolButton#help {background:white;border:1px solid #ced6e1;border-radius:16px;min-width:32px;min-height:32px;font-size:16px;font-weight:700;color:#0078d4;} QToolButton#help:hover {background:#e8f2ff;border-color:#0078d4;} QToolButton#help::menu-indicator {image:none;width:0px;} QLabel#summary {font-size:15px;padding-left:14px;} QFrame#toast {background:white;border:1px solid #cfd8e3;border-radius:10px;} QTableWidget {background:white;gridline-color:#edf0f5;} QHeaderView::section {background:#edf3fa;border:0;padding:10px;}'''
def selftest(out):
    """Packaged-build check (`--selftest <file.json>`): Korean holidays, a calculation, help text and a window render.
    Uses a temporary folder so the real autosave and prefs are never touched."""
    global STORE
    import tempfile, traceback
    STORE=Path(tempfile.mkdtemp())/'autosave.json'
    try:
        app=QApplication(sys.argv[:1]);app.setStyle('Fusion');app.setStyleSheet(STYLE)
        w=Main();w.resize(1200,900);hs=holiday_map(w.p,2026);r=calculate(w.p,'demo',2026,10)
        w.grab().save(str(Path(out).with_suffix('.png')))
        result={'holidays_2026':len(hs),'chuseok':hs.get('2026-09-25'),'pay_2026_10':r['pay'],'salary_year':salary_table(w.p)[0],'help_chars':len(render('도움말')[0]),'intro_chars':len(render('시작 안내')[0]),'docs':len(list(doc_dir().glob('*.md'))),'version':VERSION,'title':w.windowTitle()}
    except Exception:result={'error':traceback.format_exc()}
    Path(out).write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')

def run():
    if len(sys.argv)>=3 and sys.argv[1]=='--selftest':return selftest(sys.argv[2])
    if len(sys.argv)>=4 and sys.argv[1]=='--finish-update':
        exe=updater.finish(sys.argv[2],sys.argv[3]);subprocess.Popen([str(exe),'--updated']);return
    app=QApplication(sys.argv);app.setStyle('Fusion');app.setStyleSheet(STYLE);p=None;error=None
    if STORE.exists():
        try:p=load_project(STORE)
        except Exception as e:
            error=str(e)
            import shutil
            shutil.copy2(STORE,STORE.with_name('autosave_unreadable_'+dt.datetime.now().strftime('%Y%m%d_%H%M%S')+'.json'))
    window=Main(p);window.show()
    if error:QMessageBox.warning(window,'이전 저장파일 읽기 실패','읽지 못한 저장파일의 백업을 별도로 보관했습니다. 예제 화면으로 시작합니다.\n'+error)
    window.startup()
    if '--updated' in sys.argv:window.statusBar().showMessage(f'v{VERSION}로 업데이트했습니다. 이전 버전은 "_이전버전" 폴더에 남아 있습니다.',15000)
    sys.exit(app.exec())
if __name__=='__main__':run()
