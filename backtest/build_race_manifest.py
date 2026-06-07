# coding: utf-8
"""
バックテスト対象レースのマニフェスト生成スクリプト

netkeiba の開催カレンダー（calendar.html）から開催日を新しい順に発見し、
開催日別レース一覧（AJAX エンドポイント race_list_sub.html）を辿って
その日に行われた「重賞（G1/G2/G3）」の race_id とレース情報を
backtest/race_manifest.json に保存する。

既存マニフェストを土台に新しい開催日から順に追記し、合計が TARGET_RACE_COUNT
（300レース）に達した時点で打ち切る。不足する場合は OLDEST_KAISAI_YEAR
（2021年）まで遡る。

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
import datetime
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

# 月別の開催カレンダー。開催日（kaisai_date）へのリンクを含む生 HTML を返すため、
# 当て推量の日付で race_list_sub を叩かずに済む（実在開催日のみを辿る）。
CALENDAR_URL: Final[str] = 'https://race.netkeiba.com/top/calendar.html'

# マニフェストの目標レース数。新しい開催日から順に追記し、ここに達したら打ち切る。
TARGET_RACE_COUNT: Final[int] = 300

# 遡り下限。この年より前の開催日は対象にしない。
OLDEST_KAISAI_YEAR: Final[int] = 2021

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


# カレンダーページ内の開催日リンク（race_list.html?kaisai_date=YYYYMMDD）を拾う
_KAISAI_DATE_PATTERN: Final = re.compile(r'kaisai_date=(\d{8})')


def scraping_kaisai_dates(year: int, month: int) -> List[str]:
  """1ヶ月分の開催カレンダーから実在する開催日（YYYYMMDD）を新しい順で返す。"""
  response = requests.get(
      CALENDAR_URL,
      params={'year': year, 'month': month},
      headers=SCRAPING_HEADERS,
      timeout=30,
  )
  if response.status_code != 200:
    print(f'  [WARN] HTTP {response.status_code}: calendar {year}-{month:02d}')
    return []
  kaisai_dates = set(_KAISAI_DATE_PATTERN.findall(response.text))
  # 当月以外の日付（前後月のリンク等）が混入しても弾く
  month_prefix = f'{year}{month:02d}'
  return sorted(
      (d for d in kaisai_dates if d.startswith(month_prefix)), reverse=True)


def _iter_months_newest_first(today: datetime.date):
  """今日の月から OLDEST_KAISAI_YEAR の1月まで (year, month) を新しい順に返す。"""
  year, month = today.year, today.month
  while year >= OLDEST_KAISAI_YEAR:
    yield year, month
    month -= 1
    if month == 0:
      year, month = year - 1, 12


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


def _save_manifest(manifest: dict) -> None:
  with open(RACE_MANIFEST_PATH, 'w', encoding='utf-8') as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)


def main():
  today = datetime.date.today()
  # 既存マニフェストを土台に追記する（重複 race_id は最新の発見結果で上書き＝同値）。
  manifest: dict = _load_existing_manifest()
  print(f'既存マニフェスト: {len(manifest)} 件（これを土台に {TARGET_RACE_COUNT} 件まで追記します）')

  # 既にマニフェストに含まれる開催日は、その日の重賞を取得済みなのでスキップしてよい
  # （scraping_graded_races は1開催日の重賞を全件返すため）。
  collected_dates = {race['kaisai_date'] for race in manifest.values()}

  for year, month in _iter_months_newest_first(today):
    if len(manifest) >= TARGET_RACE_COUNT:
      break
    try:
      kaisai_dates = scraping_kaisai_dates(year, month)
    except Exception as error:
      print(f'[WARN] カレンダー取得で例外 ({year}-{month:02d}): {error}')
      time.sleep(SCRAPING_INTERVAL_SECS)
      continue
    time.sleep(SCRAPING_INTERVAL_SECS)

    for kaisai_date in kaisai_dates:
      if len(manifest) >= TARGET_RACE_COUNT:
        break
      if kaisai_date >= today.strftime('%Y%m%d'):
        continue  # 当日・未来の開催はまだ結果が確定していないため対象外
      if kaisai_date in collected_dates:
        continue  # 取得済みの開催日
      print(f'=== kaisai_date={kaisai_date} ({len(manifest)}/{TARGET_RACE_COUNT}) ===')
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
      collected_dates.add(kaisai_date)
      # 1開催日ごとに保存し、途中失敗しても発見済み分を取りこぼさない
      _save_manifest(manifest)
      time.sleep(SCRAPING_INTERVAL_SECS)

  _save_manifest(manifest)
  print()
  print(f'完了: {len(manifest)} 件の重賞を {RACE_MANIFEST_PATH} に保存しました')


if __name__ == '__main__':
  main()
