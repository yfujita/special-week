# coding: utf-8
"""
バックテスト対象レースのマニフェスト生成スクリプト

netkeiba の開催日別レース一覧（AJAX エンドポイント race_list_sub.html）を辿り、
指定した開催日に行われた「重賞（G1/G2/G3）」の race_id とレース情報を発見して
backtest/race_manifest.json に保存する。

当て推量の race_id で 404 を量産しないため、実在が保証されている一覧ページから
race_id を発見する方式を採る（タスク指示の「堅牢な手段」）。発見できたものだけ採用し、
グレード判定できない行はスキップする。

出力JSON形式（race_id をキーとする dict）:
{
  "202406050811": {
    "race_name": "有馬記念",
    "grade": "G1",
    "distance": 2500,
    "course_type": "芝",
    "place": "中山",
    "kaisai_date": "20241222"
  },
  ...
}

race_info 相当（race_id / race_name / distance / course_type / place）を保持するため、
run_backtest.py がこのマニフェストをそのまま race_info として扱える。
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
)

_BACKTEST_DIR: Final[str] = os.path.dirname(os.path.abspath(__file__))
RACE_MANIFEST_PATH: Final[str] = os.path.join(_BACKTEST_DIR, 'race_manifest.json')

# 開催日別レース一覧の AJAX エンドポイント（JS 描画前の生 HTML を返す）
RACE_LIST_SUB_URL: Final[str] = 'https://race.netkeiba.com/top/race_list_sub.html'

# グレードアイコンのクラス名 -> グレード表記。Icon_GradeType1/2/3 が G1/G2/G3。
# 16/17 等の大きい番号は特別・リステッド競走なので重賞ではない（採用しない）。
_GRADE_ICON_TO_LABEL: Final[dict] = {
    'Icon_GradeType1': 'G1',
    'Icon_GradeType2': 'G2',
    'Icon_GradeType3': 'G3',
}

_RACE_ID_PATTERN: Final = re.compile(r'race_id=(\d{12})')
# 一覧行テキスト例: "11R 有馬記念 15:40 芝2500m 16頭" から course_type/distance を取る
_COURSE_DISTANCE_PATTERN: Final = re.compile(r'([芝ダ障])(\d{3,4})m')

# netkeiba の競馬場コード（race_id の 5〜6 桁目）。place を補完する用途。
_PLACE_CODE_TO_NAME: Final[dict] = {
    '01': '札幌', '02': '函館', '03': '福島', '04': '新潟', '05': '東京',
    '06': '中山', '07': '中京', '08': '京都', '09': '阪神', '10': '小倉',
}


def _place_from_race_id(race_id: str) -> str:
  """race_id の競馬場コード（5〜6桁目）から競馬場名を引く。不明なら空文字。"""
  return _PLACE_CODE_TO_NAME.get(race_id[4:6], '')


def _detect_grade(anchor) -> Optional[str]:
  """レース行の <a> 配下のグレードアイコンから G1/G2/G3 を判定する。重賞でなければ None。"""
  grade_span = anchor.find(class_=re.compile('Icon_GradeType'))
  if grade_span is None:
    return None
  for css_class in (grade_span.get('class') or []):
    label = _GRADE_ICON_TO_LABEL.get(css_class)
    if label is not None:
      return label
  return None


def scraping_graded_races(kaisai_date: str) -> List[dict]:
  """1開催日のレース一覧から重賞だけを抽出して race_info 相当 dict のリストで返す。"""
  response = requests.get(
      RACE_LIST_SUB_URL,
      params={'kaisai_date': kaisai_date},
      headers=SCRAPING_HEADERS,
      timeout=30,
  )
  if response.status_code != 200:
    print(f'  [WARN] HTTP {response.status_code}: kaisai_date={kaisai_date}')
    return []

  soup = BeautifulSoup(response.content, 'html.parser')

  graded_races: List[dict] = []
  seen_race_ids = set()
  for anchor in soup.select('a'):
    href = anchor.get('href', '')
    matched = _RACE_ID_PATTERN.search(href)
    if matched is None:
      continue
    race_id = matched.group(1)
    if race_id in seen_race_ids:
      continue  # 同一レースが複数 <a> に出るため最初の1件だけ採用

    grade = _detect_grade(anchor)
    if grade is None:
      continue  # 重賞でない行はスキップ

    text = anchor.get_text(' ', strip=True)
    course_matched = _COURSE_DISTANCE_PATTERN.search(text)
    if course_matched is None:
      print(f'  [WARN] コース/距離を解釈できずスキップ: race_id={race_id} text="{text}"')
      continue
    course_type = course_matched.group(1)
    distance = int(course_matched.group(2))

    # レース名は "11R 有馬記念 15:40 ..." の R番号と時刻を除いた中央部分。
    # 表記揺れに強いよう、R番号トークンを除いた先頭の非時刻トークンを名前とする。
    race_name = _extract_race_name(text)

    seen_race_ids.add(race_id)
    graded_races.append({
        'race_id': race_id,
        'race_name': race_name,
        'grade': grade,
        'distance': distance,
        'course_type': course_type,
        'place': _place_from_race_id(race_id),
        'kaisai_date': kaisai_date,
    })
  return graded_races


_RACE_NUMBER_TOKEN: Final = re.compile(r'^\d{1,2}R$')
_TIME_TOKEN: Final = re.compile(r'^\d{1,2}:\d{2}$')


def _extract_race_name(row_text: str) -> str:
  """一覧行テキストからレース名トークンを取り出す（R番号・時刻・コース表記を除く）。"""
  name_tokens: List[str] = []
  for token in row_text.split(' '):
    if _RACE_NUMBER_TOKEN.match(token):
      continue
    if _TIME_TOKEN.match(token):
      break  # 時刻以降（コース・頭数）はレース名ではない
    name_tokens.append(token)
  return ''.join(name_tokens)


# 直近2シーズン（2024〜2025）の重賞をグレード・距離・芝ダ・競馬場が多様になるよう
# 選定した開催日。各開催日には複数の重賞が含まれることが多く、ここから重賞のみ抽出する。
# 春秋の主要 G1 開催日に加え、ダート重賞・ローカル開催日を意図的に混ぜている。
TARGET_KAISAI_DATES: Final[List[str]] = [
    # --- 2024年（芝・ダート・各競馬場が多様になるよう選定） ---
    '20240225',  # 中山記念(G2,芝1800,中山) / 阪急杯(G3,芝1400,阪神)
    '20240324',  # 高松宮記念(G1,芝1200,中京) / マーチS(G3,ダ,中山)
    '20240331',  # 大阪杯(G1,芝2000,阪神)
    '20240526',  # 日本ダービー(G1,芝2400,東京) / 目黒記念(G2,芝2500)
    '20240623',  # 宝塚記念(G1,芝2200,京都)
    '20240714',  # 函館記念(G3,芝2000,函館) 等ローカル開催
    '20241027',  # 天皇賞秋(G1,芝2000,東京) / スワンS(G2)
    '20241124',  # ジャパンC(G1,芝2400,東京) / 京都2歳S 等
    '20241201',  # チャンピオンズC(G1,ダ1800,中京) ダート G1
    '20241222',  # 有馬記念(G1,芝2500,中山)
    # --- 2025年 ---
    '20250126',  # 東海S / アメリカJCC(G2,芝2200,中山) 等
    '20250223',  # フェブラリーS(G1,ダ1600,東京) / 小倉大賞典(G3,芝1800,小倉)
    '20250406',  # 大阪杯(G1,芝2000,阪神)
    '20250525',  # オークス(G1,芝2400,東京)
    '20251026',  # 菊花賞(G1,芝3000,京都) 等
    # --- 2026年 ---
    # --- 2026年 ---
    '20260531',
    '20260524',
    '20260517',
    '20260510',
    '20260503',
    '20260426',
    '20260419',
    '20260412',
    '20260405',
    # --- 2022〜2023シーズン追加（汎化性能改善のための母数拡充。Phase 4 所見＝ダート希少を是正） ---
    # ダート重賞を意図的に厚く選定（febS・平安S・武蔵野S・根岸S・ユニコーンS・シリウスS・
    # みやこS・チャンピオンズC）。芝も小倉/中京/京都/阪神/中山/東京と競馬場を分散させる。
    '20220220',  # フェブラリーS(G1,ダ1600,東京) / 小倉大賞典(G3,芝1800,小倉)
    '20220521',  # 平安S(G3,ダ1900,中京)
    '20221023',  # 菊花賞(G1,芝3000,阪神)
    '20221030',  # 天皇賞(秋)(G1,芝2000,東京)
    '20221112',  # 武蔵野S(G3,ダ1600,東京) / デイリー杯2歳S(G2,芝1600,阪神)
    '20221225',  # 有馬記念(G1,芝2500,中山)
    '20230129',  # 根岸S(G3,ダ1400,東京) / シルクロードS(G3,芝1200,中京)
    '20230219',  # フェブラリーS(G1,ダ1600,東京) / 小倉大賞典(G3,芝1800,小倉)
    '20230402',  # 大阪杯(G1,芝2000,阪神)
    '20230618',  # ユニコーンS(G3,ダ1600,東京) / マーメイドS(G3,芝2000,阪神)
    '20230930',  # シリウスS(G3,ダ2000,阪神)
    '20231105',  # アルゼンチン共和国杯(G2,芝2500,東京) / みやこS(G3,ダ1800,京都)
    '20231203',  # チャンピオンズC(G1,ダ1800,中京)
]


def _load_existing_manifest() -> dict:
  """既存の race_manifest.json を読み込む（無ければ空 dict）。

  本スクリプトは追記運用（既存21レースを残したまま新規開催日分を加える）にする。
  1開催日の取得が一時的に失敗しても、既に確定している重賞を取りこぼさないよう、
  既存マニフェストを土台にして新規発見分を上書きマージする。
  """
  if not os.path.exists(RACE_MANIFEST_PATH):
    return {}
  with open(RACE_MANIFEST_PATH, encoding='utf-8') as f:
    return json.load(f)


def main():
  print(f'対象開催日 {len(TARGET_KAISAI_DATES)} 日から重賞を発見します')
  # 既存マニフェストを土台に追記する（重複 race_id は最新の発見結果で上書き＝同値）。
  manifest: dict = _load_existing_manifest()
  print(f'既存マニフェスト: {len(manifest)} 件（これを土台に追記します）')
  for kaisai_date in TARGET_KAISAI_DATES:
    print(f'=== kaisai_date={kaisai_date} ===')
    try:
      races = scraping_graded_races(kaisai_date)
    except Exception as error:
      print(f'  [WARN] 一覧取得で例外: {error}')
      time.sleep(SCRAPING_INTERVAL_SECS)
      continue
    for race in races:
      manifest[race['race_id']] = race
      print(f'  [OK] {race["grade"]:>2} {race["race_name"]} '
            f'{race["course_type"]}{race["distance"]}m {race["place"]} '
            f'(race_id={race["race_id"]})')
    if not races:
      print('  重賞は見つかりませんでした')
    time.sleep(SCRAPING_INTERVAL_SECS)

  with open(RACE_MANIFEST_PATH, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)

  print()
  print(f'完了: {len(manifest)} 件の重賞を {RACE_MANIFEST_PATH} に保存しました')


if __name__ == '__main__':
  main()
