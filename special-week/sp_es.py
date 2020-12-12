import requests
import time
import sys
import json

class SpEs():
  def __init__(self):
    self.url = "http://elasticsearch:9200"
    self.index_name = "horses"
    self.headers = {'Content-Type': 'application/json'}

    self.INDEX_SETTING_FILE = "./es_files/index_settings_horses.json"
  
  def initilaize(self):
    while True:
      try:
        response: requests.Response = requests.get(self.url)
        if response.status_code == 200:
          break
        print('Wait elasticsearch. status:' + str(response.status_code))
        time.sleep(5)
      except requests.exceptions.ConnectionError:
        print('Wait elasticsearch. connect error')
        time.sleep(5)
      sys.stdout.flush()

    response = requests.delete(self.__index_url())
    if response.status_code != 200 and response.status_code != 404:
      raise RuntimeError('Failed to delete old index. status:' + str(response.status_code))

  def setup_index(self, race_data: dict):
    settings: str = self.__load_settings()
    response = requests.put(self.__index_url(), data=settings, headers=self.headers)
    if response.status_code != 200:
      raise RuntimeError('Failed to create index. status:' + str(response.status_code) + " " + response.text)

    for horse in race_data['horses']:
      performances = []
      for performance in horse['horse_performance']:
        performances.append(performance)

      doc_horse: dict = {
        'name': horse['horse_name'],
        'pos': horse['pos'],
        'sex': horse['sex'],
        'age': horse['age'],
        'additional_weight': horse['additional_weight'],
        'performance': performances,
        'performance_score': horse['performance_score']
      }
      print('Put ' + str(doc_horse))
      response = requests.put(self.__index_url() + '/_doc/' + str(horse['pos']), data=json.dumps(doc_horse), headers=self.headers)
      if response.status_code >= 400:
        raise RuntimeError('Failed to put data. status:' + str(response.status_code) + " " + response.text)
    
    requests.post(self.url + '/_refresh')
  

  def __load_settings(self) -> str:
    with open(self.INDEX_SETTING_FILE) as f:
      s: str = f.read()
    return s

  def __index_url(self) -> str:
    return self.url + '/' + self.index_name