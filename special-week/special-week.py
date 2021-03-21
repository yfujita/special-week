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

def parse_arg() -> dict:
  parser = argparse.ArgumentParser()
  parser.add_argument('--race_info', help='race-infoファイル名')
  args = parser.parse_args()
  d: dict = {
    'race_info': args.race_info,
  }
  return d

def load_race_info(race_info_name: str) -> dict:
  with open(PATH_RACE_INFO + race_info_name) as f:
      data = yaml.load(f, Loader=yaml.FullLoader)
  return data

def build_data(race_info: dict):
  data: dict = {}
  data['race_name'] = race_info['race_name']
  horses: list = scraping_horses(race_info, build_race_url(race_info['race_id']))

  # 予想する
  tipster: Tipster = Tipster()
  tipster.execute(race_info, horses)

  dict_list: list = []
  for horse in horses:
    dict_list.append(horse.to_dict())
  data['horses'] = dict_list
  return data

def scraping_horses(race_info: dict, race_url: str) -> list:
  horses = []
  html = requests.get(race_url)
  soup = BeautifulSoup(html.content, "html.parser")

  for tr_horse in soup.find_all('tr', class_='HorseList'):
    td_array = []
    for td in tr_horse.find_all('td'):
      td_array.append(td)

    pos = tr_horse.find('td', class_=re.compile('Umaban.')).text.strip()
    td_horseinfo = tr_horse.find('td', class_='HorseInfo')
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

def scraping_horse(target_url: str) -> list:
  html = requests.get(target_url)
  soup = BeautifulSoup(html.content, "html.parser")

  soup_result_table = soup.find(class_="db_h_race_results")
  races: list = []
  for soup_tr in soup_result_table.find('tbody').find_all("tr"):
    race_result: RaceResult = scraping_horse_performance(soup_tr)
    if race_result != None:
      races.append(race_result)
  return races

def scraping_horse_performance(soup_tr) -> RaceResult:
  td_array = []
  for soup_td in soup_tr.find_all("td"):
    td_array.append(soup_td)
  if not td_array[11].text.strip().isdigit():
    return None
  return RaceResult(
    date = td_array[0].text.strip(),
    course = td_array[1].text.strip(),
    weather = td_array[2].text.strip(),
    race_name = td_array[4].text.strip(),
    race_grade = td_array[4].get('class')[0] if len(td_array[4].get('class')) > 0 else "",
    number_of_horses = int(td_array[6].text.strip()),
    popularity = td_array[10].text.strip(),
    ranking = int(td_array[11].text.strip()),
    distance = int(td_array[14].text.strip()[1:]),
    course_type = td_array[14].text.strip()[:1],
    course_condition = td_array[15].text.strip(),
    time = td_array[17].text.strip(),
    difference = float(td_array[18].text.strip() if len(td_array[18].text.strip()) > 0 else '0'),
    passing = td_array[20].text.strip()
  )

def debug_logging(message: str):
  if DEBUG_LOGGING:
    print(message)
    sys.stdout.flush()
    

if __name__ == '__main__':
  args: dict = parse_arg()
  race_info: dict = load_race_info(args['race_info'])
  race_data = build_data(race_info)
  print(race_data)
  
  sp_es = sp_es.SpEs()
  sp_es.initilaize()
  sp_es.setup_index(race_data)
  print("Finished.")
