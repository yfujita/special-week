from bs4 import BeautifulSoup
import requests
import argparse
import yaml
from typing import Final
import re
import time
import sys

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

def parse_arg() -> dict:
  parser = argparse.ArgumentParser(description='プログラムの説明')
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
  data['horses'] = scraping_horses(race_info, build_race_url(race_info['race_id']))
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
    performance = scraping_horse(horse_url)
    sex = tr_horse.find('td', class_='Barei').text.strip()[:1]
    age = tr_horse.find('td', class_='Barei').text.strip()[1:]
    additional_weight = td_array[5].text.strip()
    horse = {
      'pos': pos,
      'horse_name': horse_name,
      'sex': sex,
      'age': age,
      'additional_weight': additional_weight,
      'horse_performance': performance,
    }
    horse['performance_score'] = calculate_performance_score(race_info, horse)
    horses.append(horse)
    time.sleep(2)
  
  return horses

def build_race_url(id: str):
  return 'https://race.netkeiba.com/race/shutuba.html?race_id=' + str(id)

def scraping_horse(target_url: str) -> list:
  html = requests.get(target_url)
  soup = BeautifulSoup(html.content, "html.parser")

  soup_result_table = soup.find(class_="db_h_race_results")
  races: list = []
  for soup_tr in soup_result_table.find('tbody').find_all("tr"):
    race_info: dict = scraping_horse_performance(soup_tr)
    races.append(race_info)
  return races

def scraping_horse_performance(soup_tr) -> dict:
  td_array = []
  for soup_td in soup_tr.find_all("td"):
    td_array.append(soup_td)
  return {
    FIELD_DATE: td_array[0].text.strip(),
    FIELD_COURSE: td_array[1].text.strip(),
    FIELD_WEATHER: td_array[2].text.strip(),
    FIELD_RACE_NAME: td_array[4].text.strip(),
    FIELD_RACE_GRADE: td_array[4].get('class')[0] if len(td_array[4].get('class')) > 0 else "",
    FIELD_POPULARITY: td_array[10].text.strip(),
    FIELD_RANKING: int(td_array[11].text.strip()),
    FIELD_DISTANCE: int(td_array[14].text.strip()[1:]),
    FIELD_RACE_COURSE_TYPE: td_array[14].text.strip()[:1],
    FIELD_RACE_COURSE_CONDITION: td_array[15].text.strip(),
  }

def calculate_performance_score(race_info:dict, horse: dict) -> float:
  reversed = []
  for tmp in horse['horse_performance']:
    reversed.insert(0, tmp)

  race_distance = race_info['distance']
  score = 1
  for race_result in reversed:
    debug_logging("race:" + race_result[FIELD_RACE_NAME] + ' grade:' + race_result[FIELD_RACE_GRADE] + ' ' + str(race_result[FIELD_RANKING]) + '着')

    additional_score = 0
    # 勝利数加点
    if race_result[FIELD_RANKING] == 1:
      additional_score += 5
      debug_logging('score += 5')
    elif race_result[FIELD_RANKING] <= 3:
      additional_score += 2
      debug_logging('score += 2')
    
    # 重賞加点
    if race_result[FIELD_RANKING] == 1 and race_result[FIELD_RACE_GRADE] == 'rank_1':
      additional_score += 30
      debug_logging('score += 30')
    elif race_result[FIELD_RANKING] <= 3 and race_result[FIELD_RACE_GRADE] == 'rank_1':
      additional_score += 15
      debug_logging('score += 15')
    elif race_result[FIELD_RANKING] == 1 and race_result[FIELD_RACE_GRADE] == 'rank_2':
      additional_score += 20
      debug_logging('score += 20')
    elif race_result[FIELD_RANKING] <= 3 and race_result[FIELD_RACE_GRADE] == 'rank_2':
      additional_score += 7
      debug_logging('score += 10')
    elif race_result[FIELD_RANKING] == 1 and race_result[FIELD_RACE_GRADE] == 'rank_3':
      additional_score += 10
      debug_logging('score += 15')
    elif race_result[FIELD_RANKING] <= 3 and race_result[FIELD_RACE_GRADE] == 'rank_3':
      additional_score += 3
      debug_logging('score += 5')
    
    distance_rate = 800 / (800 + abs(race_distance - race_result[FIELD_DISTANCE]))
    debug_logging('distance_rate:' + str(distance_rate))
    additional_score = additional_score * distance_rate

    score += additional_score
    # 負け減点
    if race_result[FIELD_RANKING] > 5:
      score /= 2
      debug_logging('score /= 2')
    elif race_result[FIELD_RANKING] > 3:
      score /= 1.2
      debug_logging('score /= 1.2')
    elif race_result[FIELD_RANKING] > 1:
      score /= 1.1
      debug_logging('score /= 1.1')
  
  return score

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
