# coding: utf-8
"""
オフライン戦績キャッシュ収集スクリプト

各対象レースの出走馬リストと各馬の過去戦績を取得し、
backtest/fixtures/<race_id>/horses.json に保存する。
これによりバックテストをネットワーク非依存で繰り返し実行できる。

スクレイピングは test_derby2026.py の scraping_horse（AJAX方式・33列対応・馬単位）を再利用する。
ただし「1頭の戦績テーブルに空セルがあると int() 変換で例外になり、レース全体が落ちる」
という Phase1 のジャパンC 取得失敗（0002 で既知）を避けるため、本スクリプトは
出走表のパースと馬ごとの戦績取得を **馬単位の try/except で囲み**、
壊れた1頭はスキップして残りの馬で fixtures を成立させる堅牢な収集ループを持つ。

出力JSON形式（馬ごと、Horse.to_dict() 相当）:
[
  {
    "pos": "9", "horse_name": "...", "sex": "牡", "age": "4",
    "additional_weight": 57, "score": -1,
    "race_results": [ {"date": "2021/05/22", ...}, ... ]
  },
  ...
]

注意（データリーク防止）: ここではキャリア全戦績をそのまま保存する。
対象レース日より後の戦績を除外するフィルタは run_backtest.py 側で適用する
（fixtures はキャッシュとして「素のデータ」を保持し、フィルタは評価時に行う方針）。
"""
import json
import os
import re
import sys
import time
from typing import Final, List, Optional

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraping_common import (  # noqa: E402
    SCRAPING_HEADERS,
    SCRAPING_INTERVAL_SECS,
    Horse,
    build_shutuba_url,
    load_backtest_targets,
)
# 戦績テーブル1行のパース関数を直接再利用（重複実装を避ける）。
# 馬の AJAX 戦績取得は scraping_horse をそのまま使わず、行ごとに try する堅牢版を持つ。
from test_derby2026 import (  # noqa: E402
    AJAX_HEADERS,
    AJAX_HORSE_RESULTS_URL,
    _extract_horse_id,
    scraping_horse_performance,
)

FIXTURES_DIR: Final[str] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures')

_UMABAN_PATTERN: Final = re.compile('Umaban.')


def _scraping_horse_robust(horse_url: str) -> list:
  """
  1頭の過去戦績を AJAX から取得する堅牢版。

  test_derby2026.scraping_horse と同じ AJAX 呼び出しだが、戦績テーブルの各行を
  **1行ごとに try/except** でパースする。空セル（旧出走表・古い戦績で number_of_horses や
  distance のセルが空）で int() 変換が落ちても、その1戦だけ捨てて他の戦績は残す。
  Phase1 のジャパンC 取得失敗（0002 既知）の根本対策。
  """
  horse_id = _extract_horse_id(horse_url)
  ajax_headers = {**AJAX_HEADERS, 'Referer': f'https://db.netkeiba.com/horse/{horse_id}/'}
  response = requests.get(
      AJAX_HORSE_RESULTS_URL,
      params={'input': 'UTF-8', 'output': 'json', 'id': horse_id},
      headers=ajax_headers,
      timeout=30,
  )
  try:
    payload = response.json()
  except Exception:
    return []
  if payload.get('status') != 'OK' or not payload.get('data'):
    return []

  result_table = BeautifulSoup(payload['data'], 'html.parser').find(class_='db_h_race_results')
  if result_table is None:
    return []
  tbody = result_table.find('tbody')
  if tbody is None:
    return []

  races = []
  for soup_tr in tbody.find_all('tr'):
    try:
      race_result = scraping_horse_performance(soup_tr)
    except Exception:
      # 空セルを含む古い戦績行は捨てる（この1戦のみスキップ）
      continue
    if race_result is not None:
      races.append(race_result)
  return races


def _scraping_horses_robust(shutuba_url: str) -> List[Horse]:
  """
  出走表から各馬＋戦績を収集する堅牢版。

  test_derby2026.scraping_horses とロジックは同じだが、
  1頭分の取得（出走表セルのパース・AJAX戦績取得）を try/except で囲み、
  壊れた行は **その馬だけスキップ** して残りの馬を返す（レース全体を落とさない）。
  Phase1 のジャパンC（旧出走表に空セルがあり int() で全体が落ちた）対策。
  """
  response = requests.get(shutuba_url, headers=SCRAPING_HEADERS, timeout=30)
  soup = BeautifulSoup(response.content, 'html.parser')
  horse_rows = soup.find_all('tr', class_='HorseList')
  print(f'  出走表から {len(horse_rows)} 行を検出')

  horses: List[Horse] = []
  skipped_horses = 0
  for tr_horse in horse_rows:
    horse = _parse_one_horse(tr_horse)
    if horse is None:
      skipped_horses += 1
      continue
    horses.append(horse)
    time.sleep(1)  # AJAX 取得ごとに netkeiba 負荷配慮

  if skipped_horses:
    print(f'  [WARN] パース不能な {skipped_horses} 頭をスキップしました（空セル等）')
  return horses


def _parse_one_horse(tr_horse) -> Optional[Horse]:
  """出走表1行から Horse を作る。パース不能なら None（呼び出し側でスキップ）。"""
  try:
    umaban_td = tr_horse.find('td', class_=_UMABAN_PATTERN)
    td_horseinfo = tr_horse.find('td', class_='HorseInfo')
    if umaban_td is None or td_horseinfo is None:
      return None  # 小計行・ヘッダ行など

    td_array = list(tr_horse.find_all('td'))
    pos = umaban_td.text.strip()
    horse_name = td_horseinfo.text.strip()
    print(f'    Loading... {pos}:{horse_name}', flush=True)

    horse_url = td_horseinfo.find('a').get('href')
    # 戦績は行ごとに try する堅牢版で取得（空セルを含む古い戦績があってもこの馬を守る）。
    race_results = _scraping_horse_robust(horse_url)

    barei_td = tr_horse.find('td', class_='Barei')
    barei_text = barei_td.text.strip() if barei_td is not None else ''
    sex = barei_text[:1]
    age = barei_text[1:]
    # 斤量セルは旧出走表で空のことがあるが、Horse.__init__ が 0 フォールバックを持つ。
    additional_weight = td_array[5].text.strip() if len(td_array) > 5 else ''

    return Horse(
        pos=pos,
        horse_name=horse_name,
        sex=sex,
        age=age,
        additional_weight=additional_weight,
        race_results=race_results,
    )
  except Exception as error:
    print(f'    [WARN] 馬行のパースに失敗（スキップ）: {error}')
    return None


def collect_fixture(race_id: str) -> int:
  """1レース分の出走馬＋戦績をスクレイピングして保存する。保存頭数を返す（0なら失敗）。"""
  shutuba_url = build_shutuba_url(race_id)
  print(f'  出走表URL: {shutuba_url}')
  try:
    horses = _scraping_horses_robust(shutuba_url)
  except Exception as error:
    print(f'  [WARN] スクレイピング例外: {error}')
    return 0

  if not horses:
    print(f'  [WARN] 出走馬が0頭。race_id={race_id} はスキップ対象')
    return 0

  race_dir = os.path.join(FIXTURES_DIR, race_id)
  os.makedirs(race_dir, exist_ok=True)
  output_path = os.path.join(race_dir, 'horses.json')
  with open(output_path, 'w', encoding='utf-8') as f:
    json.dump([h.to_dict() for h in horses], f, ensure_ascii=False, indent=2)
  print(f'  [OK] {len(horses)}頭 -> {output_path}')
  return len(horses)


def main():
  os.makedirs(FIXTURES_DIR, exist_ok=True)
  # race-info/*.yml と race_manifest.json をマージした全対象を回す（共通ローダ）。
  targets = load_backtest_targets()

  success_count = 0
  skipped: list = []
  for label, race_id, _race_info in targets:
    print(f'=== {label} (race_id={race_id}) ===')

    # 既に取得済みならネットワーク負荷を避けて再取得しない（重複再取得は不要）。
    output_path = os.path.join(FIXTURES_DIR, race_id, 'horses.json')
    if os.path.exists(output_path):
      print(f'  [SKIP] 取得済み: {output_path}')
      success_count += 1
      continue

    if collect_fixture(race_id) > 0:
      success_count += 1
    else:
      skipped.append(race_id)
    # 1馬ごとに sleep 済みだが、レース間にも間隔を空ける
    time.sleep(SCRAPING_INTERVAL_SECS)

  print()
  print(f'完了: {success_count}/{len(targets)} レースの戦績キャッシュを保存しました')
  if skipped:
    print(f'スキップ {len(skipped)} 件: {skipped}')


if __name__ == '__main__':
  main()
