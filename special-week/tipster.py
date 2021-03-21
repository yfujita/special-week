# coding: utf-8
from logging import INFO, StreamHandler, basicConfig, getLogger

class Tipster:
  _logger = getLogger(__name__)

  def execute(self, race_info:dict, horses: list):
    for horse in horses:
      score = self._get_win_score(race_info, horse)
      self._logger.info('horse:' + horse.horse_name + ' score:' + str(score))
      horse.setScore(score)

  def _get_win_score(self, race_info:dict, horse: dict) -> float:
    #race_infoを過去順に並び替える
    reversed_result = []
    for tmp in horse.race_results:
      reversed_result.insert(0, tmp)

    race_distance: int = race_info['distance']
    total_score: float = 0
    for race_result in reversed_result:
      race_score: float = 0

      # 勝利数加点
      if race_result.ranking == 1:
        race_score += 5
      elif race_result.ranking <= 3:
        race_score += 2
      
        # 重賞加点
      if race_result.ranking == 1 and race_result.race_grade == 'rank_1':
        race_score += 30
      elif race_result.ranking <= 3 and race_result.race_grade == 'rank_1':
        race_score += 15
      elif race_result.ranking <= 5 and race_result.race_grade == 'rank_1':
        race_score += 7
      elif race_result.ranking == 1 and race_result.race_grade == 'rank_2':
        race_score += 20
      elif race_result.ranking <= 3 and race_result.race_grade == 'rank_2':
        race_score += 7
      elif race_result.ranking <= 5 and race_result.race_grade == 'rank_2':
        race_score += 4
      elif race_result.ranking == 1 and race_result.race_grade == 'rank_3':
        race_score += 15
      elif race_result.ranking <= 3 and race_result.race_grade == 'rank_3':
        race_score += 5
      elif race_result.ranking <= 5 and race_result.race_grade == 'rank_3':
        race_score += 3
    
      # 着差加点
      if race_result.ranking == 1:
        difference: float = abs(race_result.difference)
        race_score += race_score * difference
        
      distance_rate = 1000 / (1000 + abs(race_distance - race_result.distance))
      race_score = race_score * distance_rate

      total_score += race_score

      # 負け減点
      if race_result.ranking > 5:
        total_score /= 2
      elif race_result.ranking > 3:
        total_score /= 1.2
      elif race_result.ranking > 1:
        total_score /= 1.1
    
    return total_score
