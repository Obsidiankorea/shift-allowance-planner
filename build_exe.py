"""Build the Windows EXE, check the packaged program, then publish it to 배포/교대근무수당플래너/ and a versioned zip.

Run with the project venv:  .venv\\Scripts\\python.exe build_exe.py   (or double-click build_exe.cmd)
Add `--release` to also publish GitHub release v<VERSION> (gh CLI, repository in version.py) with the zip attached.
Each build uses a fresh build/dist_<time> folder and 배포/ is only replaced after the self-tests pass, so a running
copy of the program (which locks its own files) never leaves a folder half-deleted or blocks the build.
"""
import datetime as dt, filecmp, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path
from version import VERSION, REPO

ROOT=Path(__file__).resolve().parent
NAME='교대근무수당플래너'
STAMP=f'{dt.datetime.now():%Y%m%d_%H%M%S}'
STAGE=ROOT/'build'/f'dist_{STAMP}'
DIST=ROOT/'배포'

def selftest(folder):
    """Run the EXE in self-test mode (temporary data folder, real autosave untouched) and return its report."""
    report=Path(tempfile.mkdtemp())/'selftest.json'
    subprocess.run([str(folder/f'{NAME}.exe'),'--selftest',str(report)],timeout=120)
    if not report.exists():sys.exit('self-test failed: the EXE did not write a report')
    result=json.loads(report.read_text(encoding='utf-8'))
    if 'error' in result:sys.exit('self-test failed inside the EXE:\n'+result['error'])
    if result.get('chuseok')!='추석' or not result.get('pay_2026_10'):sys.exit('self-test failed: Korean holidays or calculation missing in the EXE')
    if not result.get('docs') or result.get('intro_chars',0)<1000:sys.exit('self-test failed: guide documents (문서/) missing or not rendered in the EXE')
    return result

def sync_docs_back():
    """Guide documents edited inside the deployed EXE folder (✎ 편집) are copied back to the project 문서/ first,
    so a rebuild does not throw those edits away."""
    deployed=DIST/NAME/'문서'
    if not deployed.exists():return
    for src in deployed.glob('*.md'):
        dst=ROOT/'문서'/src.name
        if not dst.exists() or (not filecmp.cmp(src,dst,shallow=False) and src.stat().st_mtime>dst.stat().st_mtime):
            shutil.copy2(src,dst);print('문서 역반영 (배포 폴더에서 고친 문서):',src.name)

def publish(built):
    """Swap 배포/교대근무수당플래너 for the new build. Renaming fails cleanly if the program is running there."""
    target=DIST/NAME;DIST.mkdir(exist_ok=True)
    if target.exists():
        old=DIST/f'{NAME}_old_{dt.datetime.now():%Y%m%d%H%M%S}'
        try:target.rename(old)
        except OSError:
            print(f'\n[교체 보류] {target} 폴더의 프로그램이 실행 중입니다. 프로그램을 닫고 build_exe.cmd를 다시 실행하세요.')
            print(f'새 빌드는 {built} 에 있고, 아래 zip도 만들어졌습니다.');return False
        shutil.rmtree(old,ignore_errors=True)
    shutil.copytree(built,target);return True

def release(archive):
    """GitHub release v<VERSION> with the zip and this version's CHANGELOG section as notes."""
    log=(ROOT/'CHANGELOG.md').read_text(encoding='utf-8')
    m=re.search(r'^## '+re.escape(VERSION)+r'\b.*?(?=^## |\Z)',log,re.S|re.M)
    notes=m.group(0) if m else f'v{VERSION}'
    subprocess.check_call(['gh','release','create',f'v{VERSION}',str(archive),'--repo',REPO,'--title',f'v{VERSION}','--notes',notes])
    print('GitHub 릴리스 게시:',f'https://github.com/{REPO}/releases/tag/v{VERSION}')

def main():
    sync_docs_back()
    subprocess.check_call([sys.executable,'-m','pip','install','-q','-r',str(ROOT/'requirements-build.txt')])
    subprocess.check_call([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--windowed','--name',NAME,
                           '--collect-all','holidays',  # country modules and Korean names are loaded lazily, so PyInstaller cannot see them
                           '--exclude-module','numpy',  # optional for openpyxl and unused here
                           '--distpath',str(STAGE),'--workpath',str(ROOT/'build'/'pyinstaller'),'--specpath',str(ROOT/'build'),
                           str(ROOT/'app.py')],cwd=ROOT)
    built=STAGE/NAME;shutil.copy2(ROOT/'사용설명서.txt',built/'사용설명서.txt')
    shutil.copytree(ROOT/'문서',built/'문서')  # editable guide documents live next to the EXE, not inside _internal
    print('selftest:',json.dumps(selftest(built),ensure_ascii=False))
    # A stray empty 'numpy' folder (seen on another PC) must not break start-up; importer.py blocks openpyxl's optional numpy import.
    fake=built/'_internal'/'numpy';fake.mkdir(exist_ok=True)
    try:print('selftest with stray numpy folder:',selftest(built)['pay_2026_10'])
    finally:shutil.rmtree(fake,ignore_errors=True)
    DIST.mkdir(exist_ok=True)
    archive=shutil.make_archive(str(DIST/f'{NAME}_v{VERSION}'),'zip',root_dir=STAGE,base_dir=NAME)
    print('zip:',archive)
    if publish(built):
        print('완료:',DIST/NAME/f'{NAME}.exe')
        for old in (ROOT/'build').glob('dist*'):  # earlier staging folders; ones still in use are skipped
            if old!=STAGE:shutil.rmtree(old,ignore_errors=True)
    if '--release' in sys.argv:release(archive)

if __name__=='__main__':main()
