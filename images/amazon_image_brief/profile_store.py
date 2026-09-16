"""Version-independent project snapshots and managed copies of user assets.

Keep this directory out of application deployments. Credentials belong in .env,
never in snapshots. Asset references are relative so the library can be backed up
or moved together with its assets without retaining the old install location.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid


ASSET_FIELDS = {'product_image_paths', 'competitor_image_paths', 'logo_image_path', 'style_reference_paths',
                'reference_image', 'ai_effect_image', 'german_composite_image',
                'imported_market_data_path', 'asset_path', 'final_image'}
PREFIX = 'asset://'


class ProfileStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory).resolve()
        self.profiles = self.directory / 'profiles'
        self.assets = self.directory / 'assets'
        self.session_path = self.directory / 'last_session.json'
        self.asset_cache = {}
        self.path_assets: dict[str, str] = {}
        self.warnings: list[str] = []

    def _profile_path(self, profile_id: str) -> Path:
        if not re.fullmatch(r'[0-9a-f]{32}', profile_id):
            raise ValueError('无效的配置 ID')
        return self.profiles / f'{profile_id}.json'

    @staticmethod
    def _atomic_write(path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Retain the last valid revision, never replace it with corrupt data.
        if path.exists():
            try:
                json.loads(path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                pass
            else:
                shutil.copy2(path, path.with_suffix('.previous.json'))
        descriptor, temporary = tempfile.mkstemp(prefix=path.stem, suffix='.tmp', dir=path.parent)
        try:
            with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _keep_asset(self, name: str) -> str:
        if not name or name.startswith(PREFIX):
            return name
        path = Path(name).resolve()
        if not path.is_file():
            saved = self.path_assets.get(str(path))
            if saved and (self.assets / saved).is_file():
                self.warnings.append(f'原文件已移走，继续使用保存副本：{path.name}')
                return PREFIX + saved
            self.warnings.append(f'图片/附件不存在，已保留原路径：{Path(name).name}')
            return name
        try:
            return PREFIX + path.relative_to(self.assets).as_posix()
        except ValueError:
            pass
        stat = path.stat()
        key = (str(path), stat.st_size, stat.st_mtime_ns)
        cached = self.asset_cache.get(key)
        if cached and (self.assets / cached).is_file():
            return PREFIX + cached
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        relative = f'{digest}/{path.name}'
        destination = self.assets / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(path, destination)
        self.asset_cache[key] = relative
        self.path_assets[str(path)] = relative
        return PREFIX + relative

    def _restore_asset(self, value: str) -> str:
        if not value.startswith(PREFIX):
            return value
        path = (self.assets / value[len(PREFIX):]).resolve()
        if not path.is_relative_to(self.assets):
            raise ValueError('配置包含非法附件路径')
        if not path.is_file():
            self.warnings.append(f'配置附件缺失：{path.name}；请恢复 user_data/assets 备份。')
        return str(path)

    def _assets_in(self, value, pack: bool, field: str = ''):
        if pack and field == 'aplus_poster' and isinstance(value, dict):
            value = deepcopy(value)
            for key in ('pc', 'mobile'):
                if value.get(key):
                    value[key] = self._keep_asset(value[key])
            for chapter in value.get('chapters', []):
                for key in ('pc', 'mobile'):
                    if chapter.get(key):
                        chapter[key] = self._keep_asset(chapter[key])
            value['files'] = [self._keep_asset(path) for path in value.get('files', [])]
            return value
        if isinstance(value, dict):
            return {key: self._assets_in(item, pack, key) for key, item in value.items()}
        if isinstance(value, list):
            return [self._assets_in(item, pack, field) for item in value]
        if isinstance(value, str) and (field in ASSET_FIELDS or (not pack and value.startswith(PREFIX))):
            return self._keep_asset(value) if pack else self._restore_asset(value)
        return value

    def save(self, snapshot: dict, profile_id: str = '', name: str = '') -> str:
        self.warnings = []
        payload = self._assets_in(deepcopy(snapshot), pack=True)
        payload.update(schema_version=1, profile_id=profile_id, name=name,
                       saved_at=datetime.now().astimezone().isoformat(timespec='seconds'))
        if profile_id:
            self._atomic_write(self._profile_path(profile_id), payload)
        self._atomic_write(self.session_path, payload)
        return payload['saved_at']

    def save_as(self, snapshot: dict, name: str) -> str:
        if not name.strip():
            raise ValueError('请输入配置名称')
        profile_id = uuid.uuid4().hex
        self.save(snapshot, profile_id, name.strip())
        return profile_id

    def _read(self, path: Path) -> dict:
        try:
            result = json.loads(path.read_text(encoding='utf-8'))
        except (ValueError, OSError) as exc:
            previous = path.with_suffix('.previous.json')
            if not previous.is_file():
                raise ValueError(f'无法读取配置 {path.name}：{exc}') from exc
            result = json.loads(previous.read_text(encoding='utf-8'))
            self.warnings.append(f'{path.name} 无法读取，已恢复上一份有效副本。')
        if not isinstance(result, dict) or result.get('schema_version') != 1 or not isinstance(result.get('project'), dict):
            raise ValueError('配置版本不兼容或缺少项目数据；原文件未修改。')
        return result

    def load(self, profile_id: str = '') -> dict | None:
        self.warnings = []
        path = self._profile_path(profile_id) if profile_id else self.session_path
        if not profile_id and not path.exists() and not path.with_suffix('.previous.json').exists():
            return None
        return self._assets_in(self._read(path), pack=False)

    def list_profiles(self) -> list[dict]:
        profiles = []
        for path in self.profiles.glob('*.json'):
            if not re.fullmatch(r'[0-9a-f]{32}', path.stem):
                continue
            try:
                raw = self._read(path)
                profiles.append({'id': path.stem, 'name': raw.get('name', path.stem),
                                 'saved_at': raw.get('saved_at', '')})
            except (OSError, ValueError, TypeError):
                self.warnings.append(f'配置 {path.name} 无法读取，原文件仍保留。')
        return sorted(profiles, key=lambda item: item['saved_at'], reverse=True)
