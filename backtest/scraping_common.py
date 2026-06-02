# coding: utf-8
"""
スクレイピング共通ユーティリティ

test_derby2026.py の scraping_horses / scraping_horse / scraping_horse_performance
（AJAXエンドポイント方式・33列構造対応）を再利用するための薄いブリッジ。
重複実装を避けるため、本モジュールは test_derby2026 から関数を re-export するだけにする。

また、特殊な共通定数（HTTPヘッダ・スクレイピング間隔）もここに集約する。
"""
import json
import os
import sys
from typing import Final, List, Tuple

# プロジェクトのレイアウト:
#   special-week/                <- リポジトリルート
#     test_derby2026.py
#     special-week/              <- コード本体（tipster, horse 等）
#     backtest/                  <- 本ファイル
_PROJECT_ROOT: Final[str] = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CODE_DIR: Final[str] = os.path.join(_PROJECT_ROOT, 'special-week')

# test_derby2026 と tipster/horse を import できるようにパスを通す
for _path in (_PROJECT_ROOT, _CODE_DIR):
  if _path not in sys.path:
    sys.path.insert(0, _path)

# test_derby2026 のスクレイピング関数群を再利用（重複実装を避ける）
from test_derby2026 import (  # noqa: E402
    SCRAPING_HEADERS,
    scraping_horse,
    scraping_horse_performance,
    scraping_horses,
)
from horse import Horse, RaceResult, RaceGrade  # noqa: E402

# netkeiba への負荷配慮: リクエスト間に最低でもこの秒数を空ける
SCRAPING_INTERVAL_SECS: Final[float] = 1.0


def build_shutuba_url(race_id: str) -> str:
  """出走表ページURL（過去・未来どちらのレースでも有効）"""
  return f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'


def build_result_url(race_id: str) -> str:
  """レース結果ページURL（着順・人気・オッズが載る）"""
  return f'https://race.netkeiba.com/race/result.html?race_id={race_id}'


# --- バックテスト対象レースの一元管理 ----------------------------------------
# 対象レースは2系統ある:
#   (A) 従来からの race-info/*.yml（Phase 1 の5レース）
#   (B) 拡充分の race_manifest.json（Phase 4 前の20〜30レース拡充。本ファイルで集約）
# 収集スクリプト（collect_*）と評価（run_backtest）が同じ対象集合を回せるよう、
# ここで両系統をマージした「対象レース一覧」を1関数で提供し重複ロジックを排除する。

import yaml  # noqa: E402  （re-export 用途で末尾 import）

_PROJECT_ROOT_FOR_TARGETS: Final[str] = _PROJECT_ROOT
RACE_INFO_DIR: Final[str] = os.path.join(_PROJECT_ROOT_FOR_TARGETS, 'race-info')
_BACKTEST_DIR_FOR_TARGETS: Final[str] = os.path.dirname(os.path.abspath(__file__))
RACE_MANIFEST_PATH: Final[str] = os.path.join(_BACKTEST_DIR_FOR_TARGETS, 'race_manifest.json')


def load_backtest_targets() -> List[Tuple[str, str, dict]]:
  """
  バックテスト対象レースを (識別名, race_id, race_info) のリストで返す。

  race_info は最低でも race_name / distance / course_type / place を持ち、
  Tipster（distance を使う）と Phase3 のコース種別ペナルティ（course_type を使う）が
  そのまま参照できる形にする。

  - race-info/*.yml（race-info.yml は重複なので除外）を読み込む。
  - race_manifest.json があれば各エントリも対象に加える。
  - race_id が重複する場合は race-info/*.yml 側を優先する（既存5レースの後方互換を保つ）。
  """
  targets: List[Tuple[str, str, dict]] = []
  seen_race_ids = set()

  # (A) 従来の race-info/*.yml を優先（既存ベースラインの後方互換のため）
  if os.path.isdir(RACE_INFO_DIR):
    for file_name in sorted(os.listdir(RACE_INFO_DIR)):
      if not file_name.endswith('.yml') or file_name == 'race-info.yml':
        continue
      with open(os.path.join(RACE_INFO_DIR, file_name)) as f:
        race_info = yaml.load(f, Loader=yaml.FullLoader)
      race_id = str(race_info['race_id']).strip()
      if race_id in seen_race_ids:
        continue
      seen_race_ids.add(race_id)
      targets.append((file_name, race_id, race_info))

  # (B) 拡充分の race_manifest.json
  if os.path.exists(RACE_MANIFEST_PATH):
    with open(RACE_MANIFEST_PATH, encoding='utf-8') as f:
      manifest = json.load(f)
    for race_id, race_info in sorted(manifest.items()):
      race_id = str(race_id).strip()
      if race_id in seen_race_ids:
        continue  # race-info/*.yml 側を優先
      seen_race_ids.add(race_id)
      # マニフェスト由来は識別名に grade を付けてログを読みやすくする
      label = f'{race_info.get("grade", "")}_{race_info.get("race_name", race_id)}'
      targets.append((label, race_id, race_info))

  return targets


__all__ = [
    'SCRAPING_HEADERS',
    'SCRAPING_INTERVAL_SECS',
    'Horse',
    'RaceResult',
    'RaceGrade',
    'scraping_horse',
    'scraping_horse_performance',
    'scraping_horses',
    'build_shutuba_url',
    'build_result_url',
    'load_backtest_targets',
    'RACE_INFO_DIR',
    'RACE_MANIFEST_PATH',
]
