# coding: utf-8
from typing import Final


class Horse:
  RACE_TYPE_NIGE: Final[int] = 1
  RACE_TYPE_SENKOU: Final[int] = 2
  RACE_TYPE_SASHI: Final[int] = 3
  RACE_TYPE_OIKOMI: Final[int] = 4

  def __init__(self, pos: int, horse_name: str, sex: str, age: int, additional_weight: int, race_results: list):
    self.pos: int = pos
    self.horse_name: str = horse_name
    self.sex: str = sex
    self.age: int = age
    self.additional_weight: int = additional_weight
    self.race_results: list = race_results 
    self.score: float = -1

  def setScore(self, score: float):
    self.score = score
  
  def getRunType(self) -> int:
    position = 0
    for race_result in self.race_results:
      first_passing = race_result.passing.split('-')[0]
      position += int(first_passing) / race_result.number_of_horses

    position = position / len(self.race_results)
    if position <= 0.2:
      return self.RACE_TYPE_NIGE
    elif position <= 0.4:
      return self.RACE_TYPE_SENKOU
    elif position <= 0.7:
      return self.RACE_TYPE_SASHI
    else:
      return self.RACE_TYPE_OIKOMI
  
  def to_dict(self) -> dict:
    dic :dict = {}
    dic['pos'] = self.pos
    dic['horse_name'] = self.horse_name
    dic['sex'] = self.sex
    dic['age'] = self.age
    dic['run_type'] = self.getRunType()
    dic['additional_weight'] = self.additional_weight
    dic['score'] = self.score

    results: list = []
    for result in self.race_results:
      results.append({
        'date': result.date,
        'course': result.course,
        'weather': result.weather,
        'race_name': result.race_name,
        'race_grade': result.race_grade,
        'number_of_horses': result.number_of_horses,
        'popularity': result.popularity,
        'ranking': result.ranking,
        'distance': result.distance,
        'course_type': result.course_type,
        'course_condition': result.course_condition,
        'time': result.time,
        'difference': result.difference,
        'passing': result.passing,
      })
    dic['race_results'] = results

    return dic

class RaceResult:
  def __init__(self,
    date: str,
    course: str,
    weather: str,
    race_name: str,
    race_grade: str,
    number_of_horses: int,
    popularity: str,
    ranking: int,
    distance: int,
    course_type: str,
    course_condition: str,
    time: str,
    difference: float,
    passing: str):
    
    self.date: str = date
    self.course: str = course
    self.weather: str = weather
    self.race_name: str = race_name
    self.race_grade: str = race_grade
    self.number_of_horses: int = number_of_horses
    self.popularity: str = popularity
    self.ranking: int = ranking
    self.distance: int = distance
    self.course_type: str = course_type
    self.course_condition: str = course_condition
    self.time: str = time
    self.difference: float = difference
    self.passing: str = passing

