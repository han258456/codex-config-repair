from pathlib import Path
import hashlib
from importlib.metadata import distribution
import json
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codex_repair import __version__


def main():
    if os.name != 'nt':
        raise SystemExit('请在 Windows x64 上构建 Windows 便携包。')
    artifacts = ROOT / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    assets = ROOT / 'assets'
    assets.mkdir(exist_ok=True)
    from PySide6.QtWidgets import QApplication
    from codex_repair.window import app_icon
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    app = QApplication([])
    assert app_icon(256).pixmap(256, 256).save(str(assets / 'app.ico'))
    command = [sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--noupx', '--onedir', '--windowed',
               '--name', 'CodexConfigRepair', '--icon', str(assets / 'app.ico'),
               '--distpath', str(ROOT / 'dist'), '--workpath', str(ROOT / 'build'), '--specpath', str(artifacts),
               '--exclude-module', 'PyQt6', '--exclude-module', 'PyQt5', '--exclude-module', 'PySide2',
               '--exclude-module', 'tkinter', '--paths', str(ROOT), str(ROOT / 'main.py')]
    print('Building Windows application...', flush=True)
    # Prevent unrelated PATH software (Git, Node, etc.) from supplying a different
    # ICU DLL with the same name as the Windows system ICU used by Qt.
    build_environment = os.environ.copy()
    windows = Path(os.environ.get('WINDIR', r'C:\Windows'))
    import PySide6
    build_environment['PATH'] = os.pathsep.join(map(str, [Path(sys.base_prefix), Path(sys.prefix) / 'Scripts', windows / 'System32', windows, Path(PySide6.__file__).parent]))
    with (artifacts / 'build.log').open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=ROOT, env=build_environment, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise SystemExit('构建失败，请查看 artifacts/build.log。')
    bundle = ROOT / 'dist' / 'CodexConfigRepair'
    for name in ['README.md', 'QUICK_START.txt', 'LICENSE', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md']:
        shutil.copy2(ROOT / name, bundle / name)
    licenses = bundle / 'licenses'
    licenses.mkdir(exist_ok=True)
    versions = {}
    for name in ['PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6', 'tomlkit', 'psutil', 'PyInstaller']:
        dist = distribution(name)
        versions[name] = {'version': dist.version, 'license': dist.metadata.get('License-Expression') or dist.metadata.get('License')}
        for file in dist.files or []:
            entry = str(file).lower()
            if '.dist-info/' in entry and ('license' in entry or entry.endswith('copying')):
                destination = licenses / name / Path(file).name
                destination.parent.mkdir(exist_ok=True)
                shutil.copy2(dist.locate_file(file), destination)
    python_license = Path(sys.base_prefix) / 'LICENSE.txt'
    if python_license.is_file():
        shutil.copy2(python_license, licenses / 'Python-LICENSE.txt')
    for name in ['LGPL-3.0.txt', 'GPL-3.0.txt']:
        cache = ROOT / 'licenses' / name
        if not cache.exists():
            raise SystemExit('缺少随源码提供的许可证文件：' + name)
        shutil.copy2(cache, licenses / name)
    versions['Python'] = {'version': sys.version.split()[0], 'license': 'PSF'}
    (licenses / 'DEPENDENCIES.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
    print('Verifying the packaged executable on synthetic data...', flush=True)
    smoke = subprocess.run([str(bundle / 'CodexConfigRepair.exe'), '--smoke-test', '--screenshot', str(artifacts / 'packaged-preview.png')], cwd=ROOT, timeout=180)
    if smoke.returncode:
        raise SystemExit('打包程序的界面烟雾测试失败，未生成发布 ZIP。')
    release = ROOT / 'release'
    release.mkdir(exist_ok=True)
    portable = release / f'CodexConfigRepair-{__version__}-windows-x64.zip'
    with zipfile.ZipFile(portable, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                archive.write(path, Path('CodexConfigRepair') / path.relative_to(bundle))
    source = release / f'CodexConfigRepair-{__version__}-source.zip'
    with zipfile.ZipFile(source, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name in ['main.py', 'README.md', 'QUICK_START.txt', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'THIRD_PARTY_NOTICES.md', 'requirements.txt', 'requirements-build.txt', '.gitignore', '.gitattributes']:
            archive.write(ROOT / name, Path('codex-config-repair') / name)
        for folder in ['codex_repair', 'tests', 'scripts', 'assets', 'licenses']:
            for path in sorted((ROOT / folder).rglob('*')):
                if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc':
                    archive.write(path, Path('codex-config-repair') / path.relative_to(ROOT))
    hashes = []
    for path in [portable, source]:
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        hashes.append(f'{digest.hexdigest()}  {path.name}')
        print(f'{path.name}: {path.stat().st_size / 1024 / 1024:.1f} MB', flush=True)
    (release / 'SHA256SUMS.txt').write_text('\n'.join(hashes) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
