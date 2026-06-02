# coding: utf-8
import math
from logging import getLogger
from horse import RaceGrade
from scoring_params import ScoringParams


def _normalize_course_type(course_type: str) -> str:
  """course_type の表記差を吸収して比較できるようにする。

  RaceResult.course_type は「芝」「ダ」「障」のような単一文字、
  race-info.course_type も「芝」のような文字列で渡る。
  先頭1文字を取り出して正規化することで、将来「ダート」「芝コース」等の
  表記揺れが来ても安定して比較できる。空文字・None は '' を返す。
  """
  if not course_type:
    return ''
  return course_type.strip()[0]


class Tipster:
  _logger = getLogger(__name__)

  def __init__(self, params: ScoringParams = None):
    # params 未指定時はデフォルト値（リファクタ前の直書き値と完全一致）を使う
    self.params: ScoringParams = params if params is not None else ScoringParams()

  def execute(self, race_info: dict, horses: list):
    for horse in horses:
      score = self._get_win_score(race_info, horse)
      self._logger.info('horse:' + horse.horse_name + ' score:' + str(score))
      horse.setScore(score)

  def _get_win_score(self, race_info: dict, horse: dict) -> float:
    # race_results は新しい順で格納されているため、過去順（古い順）に並び替える
    reversed_result = list(reversed(horse.race_results))

    params: ScoringParams = self.params
    race_distance: int = race_info['distance']

    # 対象レースのコース種別（芝/ダ/障）を正規化。Phase 3 のコース種別不一致ペナルティで使う。
    target_course_type: str = _normalize_course_type(race_info.get('course_type', ''))
    # 対象レースの当日馬場状態。race-info には無いため通常 None。
    # 供給された場合のみ Phase 3 の馬場状態一致ボーナスが効く（None ならスキップ）。
    target_course_condition = race_info.get('course_condition')

    total_score: float = 0
    for race_result in reversed_result:
      race_score: float = 0

      # 勝利数加点
      if race_result.ranking == 1:
        race_score += params.win_score_1st
      elif race_result.ranking <= 3:
        race_score += params.win_score_top3

      # 重賞加点
      if race_result.ranking == 1 and race_result.race_grade == RaceGrade.RANK_1:
        race_score += params.grade1_rank1
      elif race_result.ranking <= 3 and race_result.race_grade == RaceGrade.RANK_1:
        race_score += params.grade1_rank3
      elif race_result.ranking <= 5 and race_result.race_grade == RaceGrade.RANK_1:
        race_score += params.grade1_rank5
      elif race_result.ranking == 1 and race_result.race_grade == RaceGrade.RANK_2:
        race_score += params.grade2_rank1
      elif race_result.ranking <= 3 and race_result.race_grade == RaceGrade.RANK_2:
        race_score += params.grade2_rank3
      elif race_result.ranking <= 5 and race_result.race_grade == RaceGrade.RANK_2:
        race_score += params.grade2_rank5
      elif race_result.ranking == 1 and race_result.race_grade == RaceGrade.RANK_3:
        race_score += params.grade3_rank1
      elif race_result.ranking <= 3 and race_result.race_grade == RaceGrade.RANK_3:
        race_score += params.grade3_rank3
      elif race_result.ranking <= 5 and race_result.race_grade == RaceGrade.RANK_3:
        race_score += params.grade3_rank5

      # 着差加点（difference_multiplier=1.0 でリファクタ前と完全に等価）
      if race_result.ranking == 1:
        difference: float = abs(race_result.difference)
        race_score += race_score * difference * params.difference_multiplier

      distance_rate = params.distance_base / (params.distance_base + abs(race_distance - race_result.distance))
      race_score = race_score * distance_rate

      # --- Phase 3: 出走頭数補正 ---
      # 大頭数レースでの好走を相対的に高く評価する。
      # enable_field_size_correction=False（デフォルト）では補正を掛けないため
      # ベースラインと完全一致する。
      if params.enable_field_size_correction and race_result.number_of_horses > 1:
        field_size_rate = math.log(race_result.number_of_horses) / math.log(params.field_size_base)
        race_score = race_score * field_size_rate

      # --- Phase 3: コース種別不一致ペナルティ ---
      # 過去戦のコース種別が対象レースと異なる場合に係数（<1.0）を乗算する。
      # course_type_mismatch_penalty=1.0（デフォルト）では実質無効でベースライン一致。
      # 対象側 course_type が空のときは比較できないためスキップする。
      if params.course_type_mismatch_penalty != 1.0 and target_course_type:
        result_course_type = _normalize_course_type(race_result.course_type)
        if result_course_type and result_course_type != target_course_type:
          race_score = race_score * params.course_type_mismatch_penalty

      # --- Phase 3: 馬場状態一致ボーナス ---
      # 対象レースの当日馬場状態が供給され、かつ過去戦と一致した場合に係数を乗算する。
      # course_condition_match_bonus=1.0（デフォルト）または対象馬場状態が None のときは無効。
      if params.course_condition_match_bonus != 1.0 and target_course_condition:
        if race_result.course_condition == target_course_condition:
          race_score = race_score * params.course_condition_match_bonus

      total_score += race_score

      # 負け減点
      if race_result.ranking > 5:
        total_score /= params.penalty_rank6plus
      elif race_result.ranking > 3:
        total_score /= params.penalty_rank4_5
      elif race_result.ranking > 1:
        total_score /= params.penalty_rank2_3

    return total_score
