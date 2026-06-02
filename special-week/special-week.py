from bs4 import BeautifulSoup
import requests
import argparse
import yaml
from typing import Final
import re
import time
import sys
from tipster import Tipster
from horse import Horse, RaceResult
from scoring_params import ScoringParams, load_params_from_yaml

import sp_es

DEBUG_LOGGING: Final[bool] = True

PATH_RACE_INFO: Final[str] = '/opt/race-info/'

FIELD_DATE: Final[str] = 'date'
FIELD_COURSE: Final[str] = 'course'
FIELD_WEATHER: Final[str] = 'weather'
FIELD_RACE_NAME: Final[str] = 'race_name'
FIELD_RACE_GRADE: Final[str] = 'grade'
FIELD_POPULARITY: Final[str] = 'popularity'
FIELD_RANKING: Final[str] = 'ranking'
FIELD_DISTANCE: Final[str] = 'distance'
FIELD_RACE_COURSE_TYPE: Final[str] = 'course_type'
FIELD_RACE_COURSE_CONDITION: Final[str] = 'course_condition'
FIELD_FIELD_DIFFERENCE: Final[str] = 'time_difference'
FIELD_RUNNING_STYLE: Final[str] = 'running_style'

# netkeiba をスクレイピングする際の共通ヘッダー
# User-Agent を明示しないとブロックされる場合があるため設定する
SCRAPING_HEADERS: Final[dict] = {
  'User-Agent': (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) '
    'Chrome/120.0.0.0 Safari/537.36'
  )
}

# db.netkeiba.com の馬ページが JavaScript 必須に変わったため、
# 戦績は ajax_horse_results.html エンドポイントから取得する
AJAX_HORSE_RESULTS_URL: Final[str] = 'https://db.netkeiba.com/horse/ajax_horse_results.html'
AJAX_HEADERS: Final[dict] = {
  **SCRAPING_HEADERS,
  'Accept': 'application/json, text/javascript, */*; q=0.01',
  'X-Requested-With': 'XMLHttpRequest',
}

# scraping_horse_performance で想定するtd列の最小数（インデックス25まで使用する）
# 2025年以降の netkeiba 戦績テーブルは33列構成に変わった
EXPECTED_MIN_TD_COLS: Final[int] = 26


def parse_arg() -> dict:
  parser = argparse.ArgumentParser()
  parser.add_argument('--race_info', help='race-infoファイル名')
  # Phase 4: Optuna 最適化で得た ScoringParams(yaml) を本番予想に適用するためのフラグ。
  # 未指定時は従来どおりデフォルト ScoringParams（後方互換）。
  parser.add_argument('--params', help='ScoringParams を記述した YAML へのパス（最適化結果の適用用）')
  args = parser.parse_args()
  d: dict = {
    'race_info': args.race_info,
    'params': args.params,
  }
  return d


def load_scoring_params(params_path) -> ScoringParams:
  """--params が指定されていれば YAML から ScoringParams を読み込み、無ければデフォルトを返す。"""
  if params_path:
    params = load_params_from_yaml(params_path)
    debug_logging(f'ScoringParams を読み込みました: {params_path}')
    return params
  return ScoringParams()

def load_race_info(race_info_name: str) -> dict:
  with open(PATH_RACE_INFO + race_info_name) as f:
      data = yaml.load(f, Loader=yaml.FullLoader)
  return data

def build_data(race_info: dict, params: ScoringParams = None):
  data: dict = {}
  data['race_name'] = race_info['race_name']
  horses: list = scraping_horses(race_info, build_race_url(race_info['race_id']))

  # 予想する。params 未指定時はデフォルト ScoringParams（従来挙動・後方互換）。
  tipster: Tipster = Tipster(params)
  tipster.execute(race_info, horses)

  dict_list: list = []
  for horse in horses:
    dict_list.append(horse.to_dict())
  data['horses'] = dict_list
  return data

def scraping_horses(race_info: dict, race_url: str) -> list:
  horses = []
  html = requests.get(race_url, headers=SCRAPING_HEADERS)
  soup = BeautifulSoup(html.content, "html.parser")

  for tr_horse in soup.find_all('tr', class_='HorseList'):
    td_array = []
    for td in tr_horse.find_all('td'):
      td_array.append(td)

    # Umaban TD が存在しない行（小計行・フッター行）はスキップ
    umaban_td = tr_horse.find('td', class_=re.compile('Umaban.'))
    if umaban_td is None:
      debug_logging('Skipping row without Umaban TD')
      continue
    td_horseinfo = tr_horse.find('td', class_='HorseInfo')
    if td_horseinfo is None:
      debug_logging('Skipping row without HorseInfo TD')
      continue

    pos = umaban_td.text.strip()
    horse_name = td_horseinfo.text.strip()

    print('Loading... ' + str(pos) + ':' + horse_name)
    sys.stdout.flush()

    horse_url = td_horseinfo.find('a').get('href')
    results = scraping_horse(horse_url)
    sex = tr_horse.find('td', class_='Barei').text.strip()[:1]
    age = tr_horse.find('td', class_='Barei').text.strip()[1:]
    additional_weight = td_array[5].text.strip()
    horse = Horse(
      pos=pos,
      horse_name=horse_name,
      sex=sex,
      age=age,
      additional_weight=additional_weight,
      race_results=results
    )
    horses.append(horse)
    time.sleep(1)

  return horses

def build_race_url(id: str):
  return 'https://race.netkeiba.com/race/shutuba.html?race_id=' + str(id)

def _extract_horse_id(horse_url: str) -> str:
  """db.netkeiba.com の馬URLから horse_id を取り出す。
  例: https://db.netkeiba.com/horse/2023103687 → '2023103687'
  """
  return horse_url.rstrip('/').split('/')[-1]

def scraping_horse(target_url: str) -> list:
  # 馬ページは JavaScript 必須の SPA になっているため、
  # 戦績データは AJAX エンドポイントから直接取得する
  horse_id = _extract_horse_id(target_url)
  ajax_headers = {
    **AJAX_HEADERS,
    'Referer': f'https://db.netkeiba.com/horse/{horse_id}/',
  }
  response = requests.get(
    AJAX_HORSE_RESULTS_URL,
    params={'input': 'UTF-8', 'output': 'json', 'id': horse_id},
    headers=ajax_headers,
  )

  try:
    payload = response.json()
  except Exception:
    debug_logging(f'Failed to parse AJAX response for horse {horse_id}')
    return []

  if payload.get('status') != 'OK' or not payload.get('data'):
    # 新馬など戦績なしの場合は空リストで正常扱い
    return []

  soup_result_table = BeautifulSoup(payload['data'], 'html.parser').find(class_='db_h_race_results')
  if soup_result_table is None:
    return []

  soup_tbody = soup_result_table.find('tbody')
  if soup_tbody is None:
    return []

  races: list = []
  for soup_tr in soup_tbody.find_all('tr'):
    race_result: RaceResult = scraping_horse_performance(soup_tr)
    if race_result is not None:
      races.append(race_result)
  return races

def scraping_horse_performance(soup_tr) -> RaceResult:
  td_array = []
  for soup_td in soup_tr.find_all("td"):
    td_array.append(soup_td)

  # td 列数が想定未満の行（ヘッダー行などの異常行）はスキップする
  if len(td_array) < EXPECTED_MIN_TD_COLS:
    return None

  # 着順が数字でない行（中止・失格など）はスキップする
  if not td_array[11].text.strip().isdigit():
    return None

  # 2025年以降の戦績テーブル構造（33列）に対応したインデックス
  # 旧構造との差分:
  #   course_condition: td[15] → td[16]（馬場指数列が追加）
  #   time:             td[17] → td[18]
  #   difference:       td[18] → td[19]
  #   passing:          td[20] → td[25]（タイム指数系5列が追加）
  difference_raw = td_array[19].text.strip()
  return RaceResult(
    date = td_array[0].text.strip(),
    course = td_array[1].text.strip(),
    weather = td_array[2].text.strip(),
    race_name = td_array[4].text.strip(),
    race_grade = td_array[4].get('class')[0] if len(td_array[4].get('class') or []) > 0 else "",
    number_of_horses = int(td_array[6].text.strip()),
    popularity = td_array[10].text.strip(),
    ranking = int(td_array[11].text.strip()),
    distance = int(td_array[14].text.strip()[1:]),
    course_type = td_array[14].text.strip()[:1],
    course_condition = td_array[16].text.strip(),
    time = td_array[18].text.strip(),
    difference = float(difference_raw if difference_raw else '0'),
    passing = td_array[25].text.strip()
  )

def debug_logging(message: str):
  if DEBUG_LOGGING:
    print(message)
    sys.stdout.flush()


if __name__ == '__main__':
  args: dict = parse_arg()
  race_info: dict = load_race_info(args['race_info'])
  params: ScoringParams = load_scoring_params(args['params'])
  race_data = build_data(race_info, params)
  print(race_data)

  es = sp_es.SpEs()
  es.initialize()
  es.setup_index(race_data)
  print("Finished.")
