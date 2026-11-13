#!/usr/bin/env python3
"""Opt-in Niri Group lifecycle integration test using isolated temporary state.

Run from the Flow checkout inside a disposable Niri session:
    nix shell --inputs-from . nixpkgs#python3 -c python3 tests/niri_group_lifecycle_smoke.py
Requires Niri, Foot, an empty workspace on the focused output and no concurrent
user interaction. The test invokes this checkout's spaces and recovery CLIs
with a temporary XDG_STATE_HOME, opens only uniquely identified Foot windows,
and never writes real Flow state. It cleans up its processes and restores the
original focus when safe; it verifies window positions and real state checksums.
This does not test UI input, reboot, physical touch or multiple displays.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid

CODE = Path(__file__).resolve().parents[1] / 'session'

def niri(*args):
    return subprocess.check_output(['niri', 'msg', *map(str, args)], text=True, timeout=10)

def read(kind):
    return json.loads(niri('-j', kind))

def focus():
    return next(w for w in read('workspaces') if w['is_focused'])

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def layouts(windows):
    return {w['id']: (w.get('workspace_id'), w.get('layout', {}).get('pos_in_scrolling_layout')) for w in windows}

def wait(fn, label):
    end = time.monotonic() + 12
    while time.monotonic() < end:
        value = fn()
        if value:
            return value
        time.sleep(.1)
    raise RuntimeError(f'Timeout: {label}')

def cli(env, file, *args, expect_ok=True):
    p = subprocess.run([sys.executable, str(CODE / file), *args], env=env, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=25)
    if (p.returncode == 0) != expect_ok:
        raise RuntimeError(f'{file} {args} exit {p.returncode}: {p.stdout} {p.stderr}')
    result = json.loads(p.stdout)
    if expect_ok and 'error' in result:
        raise RuntimeError(f'{file} {args}: {result}')
    return result

def main():
    if not __debug__:
        raise RuntimeError('Run this test without Python optimization')
    original = focus()
    original_windows = read('windows')
    real_state = Path(os.environ.get('XDG_STATE_HOME') or Path.home() / '.local/state') / 'flow'
    real_files = [real_state / 'spaces.json', real_state / 'session.json', real_state / 'groups.json']
    original_digests = [digest(p) for p in real_files]
    occupied = {w.get('workspace_id') for w in original_windows}
    targets = [w for w in read('workspaces') if w.get('output') == original.get('output')
               and w['id'] != original['id'] and w['id'] not in occupied]
    if not targets:
        raise RuntimeError('No empty workspace on focused output')
    target = targets[0]
    apps = [f'flow-m1-test-{uuid.uuid4().hex}-{suffix}' for suffix in ('a', 'b')]
    processes = []
    test_workspace = None
    interrupted = False
    result = None

    def on_signal(signum, frame):
        raise KeyboardInterrupt

    old_handlers = {sig: signal.signal(sig, on_signal) for sig in (signal.SIGTERM, signal.SIGHUP)}
    def checked():
        nonlocal interrupted
        if focus()['id'] != test_workspace:
            interrupted = True
            raise RuntimeError('Workspace changed during test')
        windows = read('windows')
        if any(w.get('workspace_id') == test_workspace and w.get('app_id') not in apps for w in windows):
            interrupted = True
            raise RuntimeError('Unexpected window on test workspace')
        return windows
    def pair():
        windows = checked()
        matches = [[w for w in windows if w.get('app_id') == app and w.get('workspace_id') == test_workspace]
                   for app in apps]
        if any(len(match) > 1 for match in matches):
            raise RuntimeError('Duplicate test app ID')
        return [m[0] for m in matches] if all(matches) else None
    def grouped(expected):
        ws = pair()
        if not ws:
            return None
        positions = [w.get('layout', {}).get('pos_in_scrolling_layout') for w in ws]
        if not all(positions):
            return None
        same = positions[0][0] == positions[1][0]
        return ws if same == expected else None
    try:
        if any(w.get('workspace_id') == target['id'] for w in read('windows')):
            raise RuntimeError('Test workspace no longer empty')
        niri('action', 'focus-workspace', target['idx'])
        test_workspace = focus()['id']
        if test_workspace != target['id']:
            interrupted = True
            raise RuntimeError('Unexpected focused workspace')
        for app in apps:
            checked()
            processes.append(subprocess.Popen(['foot', '--app-id', app], stdin=subprocess.DEVNULL,
                                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            wait(lambda: any(w.get('app_id') == app for w in checked()), app)
        wins = wait(lambda: grouped(False), 'two windows')
        ids = [w['id'] for w in wins]
        niri('action', 'focus-window', '--id', ids[0])
        niri('action', 'consume-or-expel-window-left', '--id', ids[1])
        wait(lambda: grouped(True), 'initial stack')
        niri('action', 'focus-window', '--id', ids[0])
        with tempfile.TemporaryDirectory(prefix='flow-m1-state-') as tmp:
            state = Path(tmp) / 'flow'
            state.mkdir()
            env = dict(os.environ, XDG_STATE_HOME=tmp)
            legacy_id = 'legacy-' + uuid.uuid4().hex
            snapshot = {'version': 1, 'session': 'old',
                        'windows': [{'app_id': app, 'window_id': ident, 'label': app,
                                     'focused': index == 0, 'column': 1}
                                    for index, (app, ident) in enumerate(zip(apps, ids))],
                        'groups': [{'id': legacy_id, 'label': 'Old name', 'members': apps}]}
            snapshot_path = state / 'session.json'
            snapshot_bytes = json.dumps(snapshot).encode()
            snapshot_path.write_bytes(snapshot_bytes)
            registry = state / 'groups.json'
            pins = state / 'spaces.json'
            pins.write_text(json.dumps([{'id': 'legacy-pin', 'label': 'Old name',
                                         'app_id': apps[0], 'window_id': ids[0], 'window_ids': ids,
                                         'session': 'old', 'group_id': legacy_id, 'members': apps}]))
            def unchanged():
                if snapshot_path.read_bytes() != snapshot_bytes:
                    raise RuntimeError('Recovery snapshot was unexpectedly modified')
            def saved():
                return json.loads(registry.read_text())['groups']
            migrated = cli(env, 'spaces.py', 'refresh')
            assert saved()[0]['id'] == legacy_id and saved()[0]['label'] == 'Old name'
            assert any(space.get('group_id') == legacy_id for space in migrated['spaces'])
            assert migrated['pins'][0]['id'] == 'legacy-pin' and migrated['pins'][0]['group_id'] == legacy_id
            unchanged()
            cli(env, 'restore.py', 'rename-group', legacy_id, 'Renamed Group')
            assert saved()[0]['id'] == legacy_id and saved()[0]['label'] == 'Renamed Group'
            unchanged()
            pinned = cli(env, 'spaces.py', 'refresh')
            assert pinned['pins'][0]['label'] == 'Renamed Group' and pinned['pins'][0]['id'] == 'legacy-pin'
            unchanged()
            rejected = cli(env, 'spaces.py', 'dissolve-group', legacy_id, expect_ok=False)
            assert 'error' in rejected and saved()[0]['id'] == legacy_id
            unchanged()
            cli(env, 'spaces.py', 'unpin', 'legacy-pin')
            assert not json.loads(pins.read_text()) and saved()[0]['id'] == legacy_id
            pinned = cli(env, 'spaces.py', 'pin-group', legacy_id)
            assert len(pinned['pins']) == 1 and pinned['pins'][0]['group_id'] == legacy_id
            cli(env, 'spaces.py', 'unpin', pinned['pins'][0]['id'])
            cli(env, 'spaces.py', 'dissolve-group', legacy_id)
            wait(lambda: grouped(False), 'dissolved Group')
            assert not saved()
            unchanged()
            wins = pair()
            left = min(wins, key=lambda w: w['layout']['pos_in_scrolling_layout'][0])
            niri('action', 'focus-window', '--id', left['id'])
            cli(env, 'spaces.py', 'stack')
            wait(lambda: grouped(True), 'restacked windows')
            created = cli(env, 'spaces.py', 'create-group')
            groups = saved()
            assert len(groups) == 1 and groups[0]['id'] != legacy_id and set(groups[0]['members']) == set(apps)
            new_id = groups[0]['id']
            assert any(s.get('group_id') == new_id for s in created['spaces'])
            cli(env, 'restore.py', 'rename-group', new_id, 'New Group')
            assert saved()[0]['id'] == new_id and saved()[0]['label'] == 'New Group'
            def separate_pair():
                niri('action', 'focus-window', '--id', ids[1])
                niri('action', 'expel-window-from-column')
                wait(lambda: grouped(False), 'separated Group members')
            def rejoin_pair():
                niri('action', 'focus-window', '--id', ids[0])
                focused_id = focus().get('active_window_id')
                cli(env, 'spaces.py', 'rejoin-group', new_id)
                members = wait(lambda: grouped(True), 'rejoined Group members')
                assert focus().get('active_window_id') == focused_id
                assert saved()[0]['id'] == new_id
                ordered = sorted(members, key=lambda w: w['layout']['pos_in_scrolling_layout'][1])
                assert [w['app_id'] for w in ordered] == saved()[0]['members']
                assert any(s.get('group_id') == new_id for s in cli(env, 'spaces.py', 'refresh')['spaces'])
            separate_pair()
            niri('action', 'focus-window', '--id', ids[0])
            niri('action', 'move-column-right')
            wait(lambda: pair()[0]['layout']['pos_in_scrolling_layout'][0] >
                 pair()[1]['layout']['pos_in_scrolling_layout'][0], 'reversed Group members')
            rejoin_pair()
            pin2 = cli(env, 'spaces.py', 'pin-group', new_id)['pins'][0]
            assert pin2['group_id'] == new_id
            pin_id = pin2['id']
            separate_pair()
            rejoin_pair()
            pins_after_rejoin = cli(env, 'spaces.py', 'refresh')['pins']
            assert len(pins_after_rejoin) == 1 and pins_after_rejoin[0]['id'] == pin_id
            assert pins_after_rejoin[0]['group_id'] == new_id
            niri('action', 'close-window', '--id', ids[1])
            wait(lambda: not any(w.get('app_id') == apps[1] for w in checked()), 'closed Group member')
            data = Path(tmp) / 'data'
            (data / 'applications').mkdir(parents=True)
            (data / 'applications' / (apps[1] + '.desktop')).write_text(
                '[Desktop Entry]\nType=Application\nName=Flow test\nExec=' +
                shutil.which('foot') + ' --app-id ' + apps[1] + '\n')
            launch_env = dict(env, XDG_DATA_HOME=str(data), XDG_DATA_DIRS=str(data))
            incomplete = cli(launch_env, 'spaces.py', 'refresh')['incomplete_groups']
            assert any(g['id'] == new_id and g['can_open'] for g in incomplete)
            cli(launch_env, 'spaces.py', 'open-group-member', new_id)
            wins = wait(pair, 'reopened Group member')
            ids[1] = wins[1]['id']
            rejoin_pair()
            assert cli(env, 'spaces.py', 'refresh')['pins'][0]['id'] == pin_id
            cli(env, 'spaces.py', 'unpin', pin_id)
            assert saved()[0]['id'] == new_id
            cli(env, 'spaces.py', 'dissolve-group', new_id)
            wait(lambda: grouped(False), 'new Group dissolved')
            assert not saved()
            unchanged()
            result = {'result': 'pass', 'migration': True, 'snapshot_unchanged': True,
                      'legacy_group_rename_pin_unpin_dissolve': True,
                      'new_group_create_rename_rejoin_pin_unpin_dissolve': True,
                      'pinned_group_rejoin_preserves_pin': True, 'missing_member_reopened_and_rejoined': True}
    finally:
        try:
            for w in read('windows'):
                if w.get('app_id') in apps:
                    niri('action', 'close-window', '--id', w['id'])
        except (OSError, subprocess.SubprocessError):
            pass
        for process in processes:
            try:
                if process.poll() is None:
                    process.terminate()
            except ProcessLookupError:
                pass
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        try:
            if not interrupted and test_workspace is not None and focus()['id'] == test_workspace:
                live = read('windows')
                if any(w.get('id') == original.get('active_window_id') for w in live):
                    niri('action', 'focus-window', '--id', original['active_window_id'])
                else:
                    old = next((w for w in read('workspaces') if w['id'] == original['id']), None)
                    if old:
                        niri('action', 'focus-workspace', old['idx'])
        finally:
            for sig, handler in old_handlers.items():
                signal.signal(sig, handler)
    if [digest(p) for p in real_files] != original_digests:
        raise RuntimeError('A real Flow state file changed')
    if layouts(read('windows')) != layouts(original_windows):
        raise RuntimeError('Original window inventory or positions changed')
    now = focus()
    if now['id'] != original['id'] or now.get('active_window_id') != original.get('active_window_id'):
        raise RuntimeError('Original workspace or focus not restored')
    print(json.dumps(result))

if __name__ == '__main__':
    main()
