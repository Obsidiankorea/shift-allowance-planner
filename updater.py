"""업데이트 — GitHub Releases에서 새 EXE zip을 받아 프로그램 폴더를 바꾼다. 파이썬 소스 실행은 git pull --ff-only.

⚠️ 확인 실패를 '최신'으로 읽지 않는다. 모르는 것과 최신은 다르다 (내부망·오프라인 PC).
⚠️ 실행 중인 EXE는 자기 파일을 지울 수 없다. 그래서 새 EXE를 임시 폴더에서 `--finish-update`로 띄우고,
   옛 프로세스가 끝난 뒤 새 EXE가 프로그램 폴더를 교체하고 다시 켠다.
⚠️ 옛 폴더는 `<폴더>_이전버전`으로 남긴다. 그 폴더의 문서(문서/*.md)를 고쳐 썼다면 거기서 되찾을 수 있다.
⚠️ 저장 데이터(autosave·prefs)는 %LOCALAPPDATA%에 있어 교체와 무관하다.
⚠️ git 방식은 덮어쓰지 않는다 (`--ff-only`). 이 PC에서 고친 파일과 겹치면 실패하고 이유를 그대로 보여 준다.
"""
import json, os, re, shutil, subprocess, sys, tempfile, time, urllib.error, urllib.request, zipfile
from pathlib import Path
from version import VERSION, REPO

NAME='교대근무수당플래너'
API=f'https://api.github.com/repos/{REPO}/releases/latest'
ROOT=Path(__file__).resolve().parent
_NOWIN=getattr(subprocess,'CREATE_NO_WINDOW',0)
_ENV=dict(os.environ,GIT_TERMINAL_PROMPT='0',GIT_HTTP_LOW_SPEED_LIMIT='1000',GIT_HTTP_LOW_SPEED_TIME='8')
_HEADERS={'User-Agent':'shift-allowance-planner','Accept':'application/vnd.github+json'}

def parse(v):
    nums=re.findall(r'\d+',v or '')
    return tuple(int(x) for x in (nums+['0','0','0'])[:3])

def newer(latest,current=VERSION):return parse(latest)>parse(current)

def mode():
    """'exe' (frozen build: GitHub Releases), 'git' (source checkout with a remote) or 'none'."""
    if getattr(sys,'frozen',False):return 'exe'
    return 'git' if (ROOT/'.git').exists() and _git('remote',timeout=5)[1].strip() else 'none'

# ---------- EXE: GitHub Releases

def check(timeout=8):
    """Latest release: {'ok','latest','newer','notes','url','size','page'} or {'ok':False,'error'}."""
    try:
        with urllib.request.urlopen(urllib.request.Request(API,headers=_HEADERS),timeout=timeout) as r:data=json.load(r)
    except urllib.error.HTTPError as e:
        return {'ok':False,'error':'저장소나 릴리스를 찾을 수 없습니다 (비공개 저장소이거나 아직 릴리스가 없음)' if e.code==404 else f'GitHub 응답 오류 {e.code}'}
    except Exception as e:  # noqa: BLE001 - offline / blocked network
        return {'ok':False,'error':f'확인하지 못했습니다 — 인터넷 연결을 확인하세요 ({e.__class__.__name__})'}
    tag=data.get('tag_name') or '';asset=next((a for a in data.get('assets',[]) if a.get('name','').endswith('.zip')),None)
    return {'ok':True,'latest':tag.lstrip('vV'),'newer':newer(tag),'notes':data.get('body') or '','page':data.get('html_url'),
            'url':asset and asset.get('browser_download_url'),'size':asset and asset.get('size')}

def download(info,progress=None):
    """Download and unzip the release zip; returns the staged program folder (…/교대근무수당플래너)."""
    if not info.get('url'):raise RuntimeError('릴리스에 zip 파일이 없습니다')
    work=Path(tempfile.mkdtemp(prefix='sap_update_'));zpath=work/'update.zip'
    with urllib.request.urlopen(urllib.request.Request(info['url'],headers=_HEADERS),timeout=30) as r,open(zpath,'wb') as f:
        total=int(r.headers.get('Content-Length') or info.get('size') or 0);done=0
        while chunk:=r.read(1<<16):
            f.write(chunk);done+=len(chunk)
            if progress:progress(done,total)
    with zipfile.ZipFile(zpath) as z:
        for member in z.namelist():
            if not (work/member).resolve().is_relative_to(work.resolve()):raise RuntimeError('zip 안의 경로가 올바르지 않습니다')
        z.extractall(work)
    app=work/NAME
    if not (app/f'{NAME}.exe').exists():raise RuntimeError('받은 파일에 실행 파일이 없습니다')
    return app

def start_finish(staged,target=None):
    """Launch the NEW exe from the staging folder; it swaps the program folder once this process has exited."""
    target=Path(target or Path(sys.executable).parent)
    flags=getattr(subprocess,'DETACHED_PROCESS',0)|getattr(subprocess,'CREATE_NEW_PROCESS_GROUP',0)
    subprocess.Popen([str(Path(staged)/f'{NAME}.exe'),'--finish-update',str(target),str(os.getpid())],close_fds=True,creationflags=flags)

def _wait_pid(pid,timeout=60):
    if os.name!='nt' or not pid:return
    import ctypes
    h=ctypes.windll.kernel32.OpenProcess(0x00100000,False,int(pid))  # SYNCHRONIZE
    if h:ctypes.windll.kernel32.WaitForSingleObject(h,int(timeout*1000));ctypes.windll.kernel32.CloseHandle(h)

def finish(target,pid,source=None):
    """Runs inside the new exe: wait for the old process, keep the old folder as <폴더>_이전버전, copy the new build in."""
    source=Path(source or Path(sys.executable).parent);target=Path(target);_wait_pid(pid)
    backup=target.with_name(target.name+'_이전버전');shutil.rmtree(backup,ignore_errors=True)
    for _ in range(10):
        try:target.rename(backup);break
        except OSError:time.sleep(1)
    else:  # still locked (e.g. an Explorer window): keep a copy, then overwrite in place
        shutil.copytree(target,backup,dirs_exist_ok=True)
    shutil.copytree(source,target,dirs_exist_ok=True)
    return target/f'{NAME}.exe'

# ---------- source checkout: git (same approach as the 재난상황보고서 launcher)

def _git(*args,timeout=25):
    try:
        p=subprocess.run(['git',*args],cwd=ROOT,capture_output=True,timeout=timeout,text=True,encoding='utf-8',errors='replace',env=_ENV,creationflags=_NOWIN)
    except FileNotFoundError:return 127,'git이 설치되어 있지 않습니다'
    except subprocess.TimeoutExpired:return 124,f'{timeout}초 안에 응답이 없습니다 (인터넷이 막혔을 수 있습니다)'
    return p.returncode,((p.stdout or '')+(p.stderr or '')).strip()

def git_status():
    """{'ok','behind','incoming','dirty'} after `git fetch`, or {'ok':False,'error'}."""
    code,msg=_git('fetch','--quiet','origin')
    if code:return {'ok':False,'error':msg or '원격을 확인하지 못했습니다'}
    code,counts=_git('rev-list','--left-right','--count','HEAD...@{u}',timeout=15)
    behind=int(counts.split()[1]) if code==0 and len(counts.split())==2 else 0
    log=_git('log','--format=%h %s','-8','HEAD..@{u}',timeout=15)[1] if behind else ''
    dirty=[l[3:] for l in _git('status','--porcelain','--untracked-files=no',timeout=15)[1].splitlines() if l.strip()]
    return {'ok':True,'behind':behind,'incoming':[l for l in log.splitlines() if l.strip()],'dirty':dirty}

def git_pull():
    code,msg=_git('pull','--ff-only','--quiet',timeout=90)
    return {'ok':code==0,'error':None if code==0 else (msg or '받지 못했습니다')}
