# coding: utf-8
"""
正解ラベル収集スクリプト

netkeiba のレース結果ページをスクレイピングし、実際の着順を取得して
backtest/ground_truth/<race_id>.json に保存する。

出力JSON形式:
{
  "race_id": "202006050811",
  "race_date": "20201227",          # kaisai_date（YYYYMMDD）。データリーク防止フィルタに使う
  "results": [
    {"horse_name": "クロノジェネシス", "ranking": 1, "popularity": 1, "odds": 2.5},
    ...
  ]
}

作法は test_derby2026.py を踏襲（User-Agent 付与、リクエスト間に sleep）。
"""
import json
import os
import re
import sys
import time
from typing import Final, Optional

import requests
from bs4 import BeautifulSoup

# backtest 配下の共通モジュールを import するためパスを通す
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraping_common import (  # noqa: E402
    SCRAPING_HEADERS,
    SCRAPING_INTERVAL_SECS,
    build_result_url,
    load_backtest_targets,
)

GROUND_TRUTH_DIR: Final[str] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ground_truth')

# 結果テーブル（All_Result_Table）の td インデックス（result.html の構造に対応）
TD_RANKING: Final[int] = 0       # 着順
TD_HORSE_NAME: Final[int] = 3    # 馬名
TD_POPULARITY: Final[int] = 9    # 人気
TD_ODDS: Final[int] = 10         # 単勝オッズ

# kaisai_date=YYYYMMDD を URL から抽出する正規表現
_KAISAI_DATE_PATTERN: Final = re.compile(r'kaisai_date=(\d{8})')


def _extract_race_date(soup: BeautifulSoup) -> Optional[str]:
  """
  結果ページから開催日（YYYYMMDD）を取得する。

  ページ上部の日付タブ（dd.Active 内のリンク href に kaisai_date=YYYYMMDD が入る）から抽出する。
  """
  active_dd = soup.find('dd', class_='Active')
  if active_dd is not None:
    link = active_dd.find('a')
    if link is not None and link.get('href'):
      matched = _KAISAI_DATE_PATTERN.search(link.get('href'))
      if matched:
        return matched.group(1)
  return None


def _parse_int(text: str) -> Optional[int]:
  """数字以外（除外・中止等の '--' や空文字）は None を返す。"""
  cleaned = text.strip()
  return int(cleaned) if cleaned.isdigit() else None


def _parse_float(text: str) -> Optional[float]:
  cleaned = text.strip()
  try:
    return float(cleaned)
  except (ValueError, TypeError):
    return None


def scraping_ground_truth(race_id: str) -> Optional[dict]:
  """結果ページから着順・人気・オッズ・開催日を取得する。失敗時は None。"""
  result_url = build_result_url(race_id)
  response = requests.get(result_url, headers=SCRAPING_HEADERS)
  if response.status_code != 200:
    print(f'  [WARN] HTTP {response.status_code}: {result_url}')
    return None

  soup = BeautifulSoup(response.content, 'html.parser')
  result_table = soup.find('table', id='All_Result_Table')
  if result_table is None:
    print(f'  [WARN] 結果テーブルが見つかりません: race_id={race_id}')
    return None

  race_date = _extract_race_date(soup)

  results = []
  for tr in result_table.find_all('tr', class_='HorseList'):
    td_array = list(tr.find_all('td'))
    # 列数が想定未満の行（ヘッダ・注記など）はスキップ
    if len(td_array) <= TD_ODDS:
      continue

    ranking = _parse_int(td_array[TD_RANKING].text)
    if ranking is None:
      # 着順が数字でない（除外・中止・取消）はバックテスト照合対象外なのでスキップ
      continue

    results.append({
      'horse_name': td_array[TD_HORSE_NAME].text.strip(),
      'ranking': ranking,
      'popularity': _parse_int(td_array[TD_POPULARITY].text),
      'odds': _parse_float(td_array[TD_ODDS].text),
    })

  if not results:
    print(f'  [WARN] 有効な着順データが0件: race_id={race_id}')
    return None

  return {
    'race_id': race_id,
    'race_date': race_date,
    'results': results,
  }


def main():
  os.makedirs(GROUND_TRUTH_DIR, exist_ok=True)
  # race-info/*.yml と race_manifest.json をマージした全対象を回す（共通ローダ）。
  targets = load_backtest_targets()

  success_count = 0
  skipped: list = []
  for label, race_id, _race_info in targets:
    print(f'=== {label} (race_id={race_id}) ===')

    # 既に取得済みならネットワーク負荷を避けて再取得しない（重複再取得は不要）。
    output_path = os.path.join(GROUND_TRUTH_DIR, f'{race_id}.json')
    if os.path.exists(output_path):
      print(f'  [SKIP] 取得済み: {output_path}')
      success_count += 1
      continue

    try:
      ground_truth = scraping_ground_truth(race_id)
    except Exception as error:
      # 1レースの構造差で全体を止めないよう、例外はレース単位で握りつぶしてスキップ。
      print(f'  [SKIP] 例外でスキップ: {error}')
      skipped.append((race_id, str(error)))
      time.sleep(SCRAPING_INTERVAL_SECS)
      continue

    if ground_truth is None:
      print(f'  [SKIP] 正解ラベル取得に失敗しました')
      skipped.append((race_id, '結果テーブル取得失敗'))
      time.sleep(SCRAPING_INTERVAL_SECS)
      continue

    with open(output_path, 'w', encoding='utf-8') as f:
      json.dump(ground_truth, f, ensure_ascii=False, indent=2)
    print(f'  [OK] {len(ground_truth["results"])}頭 / 開催日={ground_truth["race_date"]} -> {output_path}')
    success_count += 1
    time.sleep(SCRAPING_INTERVAL_SECS)

  print()
  print(f'完了: {success_count}/{len(targets)} レースの正解ラベルを保存しました')
  if skipped:
    print(f'スキップ {len(skipped)} 件:')
    for race_id, reason in skipped:
      print(f'  - {race_id}: {reason}')


if __name__ == '__main__':
  main()
