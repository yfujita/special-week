# coding: utf-8
"""
スコアリングパラメータの集約モジュール

tipster.py の `_get_win_score()` と horse.py の `getRunType()` に直書きされていた
マジックナンバーを、単一の設定オブジェクト `ScoringParams` に集約する。

このオブジェクトは Phase 4 の ML 最適化（Optuna 等）でパラメータ空間として
そのまま探索対象になる。各フィールドのコメントには、それが tipster.py の
どの加点／減点に対応するかを明記してある（探索範囲を決める際の手がかり）。

デフォルト値は Optuna 最適化結果（backtest/best_params.yaml, 46レース最適化）を反映済み。
したがって `ScoringParams()`（引数なし）は最適化済みの挙動になる。
最適化前のベースライン（リファクタ前の直書き値）は backtest/baseline_params.yaml に退避してある。
ベースラインを再現するには run_backtest.py に --params baseline_params.yaml を渡すこと。
"""
from dataclasses import dataclass, asdict, fields
from typing import Optional


@dataclass
class ScoringParams:
  """tipster.py / horse.py のスコアリングで使用する全パラメータ。

  デフォルト値は Optuna 最適化結果（best_params.yaml, 46レース）を反映済み。
  最適化前のベースライン値は backtest/baseline_params.yaml に退避してある。
  """

  # --- 勝利数加点（tipster.py 現 L25-28） ---
  win_score_1st: float = 10.391944841883868   # 1着のとき race_score に加点
  win_score_top3: float = 3.8098291987354873  # 2-3着のとき race_score に加点

  # --- 重賞加点（tipster.py 現 L31-48） RaceGrade.RANK_1/2/3 × 着順帯（1着 / 3着内 / 5着内） ---
  grade1_rank1: float = 34.57199534120918    # G1相当(RANK_1)で1着
  grade1_rank3: float = 30.87412785667566  # G1相当(RANK_1)で3着内
  grade1_rank5: float = 21.116941894845596   # G1相当(RANK_1)で5着内
  grade2_rank1: float = 20.34638936951289  # G2相当(RANK_2)で1着
  grade2_rank3: float = 18.320950244250984   # G2相当(RANK_2)で3着内
  grade2_rank5: float = 11.12138167946345  # G2相当(RANK_2)で5着内
  grade3_rank1: float = 5.528777344760052   # G3相当(RANK_3)で1着
  grade3_rank3: float = 4.037912246467098   # G3相当(RANK_3)で3着内
  grade3_rank5: float = 1.7337949238405828  # G3相当(RANK_3)で5着内

  # --- 着差加点係数（tipster.py 現 L51-53: race_score += race_score * difference * 係数） ---
  difference_multiplier: float = 0.9615225799154037

  # --- 距離補正（tipster.py 現 L55: distance_base / (distance_base + abs(対象距離 - 戦績距離))） ---
  distance_base: float = 513.21468508759

  # --- 負け減点 除数（tipster.py 現 L61-66: total_score /= 除数） ---
  penalty_rank6plus: float = 2.1851212903222015  # 6着以降（ranking > 5）の除数
  penalty_rank4_5: float = 2.077860689556152    # 4-5着（ranking > 3）の除数
  penalty_rank2_3: float = 1.227199743517693    # 2-3着（ranking > 1）の除数

  # --- 脚質判定閾値（horse.py 現 L49-55 getRunType()。平均通過位置率の閾値） ---
  run_type_nige_threshold: float = 0.2     # この値以下なら逃げ
  run_type_senkou_threshold: float = 0.4   # この値以下なら先行
  run_type_sashi_threshold: float = 0.7    # この値以下なら差し、超えれば追込

  # ===========================================================================
  # Phase 3 追加パラメータ（未活用フィールドの活用）
  # 元は「デフォルト = 効果なし（neutral）」設計だったが、現在は Optuna 最適化結果を
  # 反映済み（neutral 値は baseline_params.yaml に退避）。各フィールドのコメントには
  # tipster.py のどの加減点に対応するかと、neutral（無効化）にする値を明記する。
  # ===========================================================================

  # --- 出走頭数補正（tipster.py: race_score に log(頭数)/log(基準頭数) を乗算） ---
  # 大頭数レースでの好走は小頭数より価値が高い、という仮説に基づくスコア補正。
  # enable_field_size_correction=False の間は補正を一切掛けない（＝neutral）。
  # 有効化時は race_score *= log(number_of_horses) / log(field_size_base) を乗算し、
  # field_size_base 頭（フルゲート想定）のレースで係数 1.0 になる。
  enable_field_size_correction: bool = False  # True で出走頭数補正を有効化（最適化結果は False）
  field_size_base: int = 16                   # 補正係数が 1.0 になる基準頭数（neutral時のフルゲート=18）

  # --- コース種別不一致ペナルティ（tipster.py: race_score に乗算） ---
  # 過去戦の course_type（芝/ダ/障）が対象レースの course_type と異なる場合に乗算する係数。
  # 1.0 = ペナルティなし（＝neutral）。0.0〜1.0 を想定（小さいほど強い減点）。
  # 最適化結果は 0.343（芝⇔ダート転戦・障害戦の戦績を強めに割り引く）。
  course_type_mismatch_penalty: float = 0.5561907754349733

  # --- 馬場状態一致ボーナス（tipster.py: race_score に乗算） ---
  # 対象レースの当日馬場状態と過去戦の馬場状態（良/稍/重/不）が一致した戦績を加点する係数。
  # デフォルト 1.0 = ボーナスなし（＝neutral）。
  # 【データ制約】対象レースの当日馬場状態は race-info / ground_truth のいずれにも無いため、
  # バックテストでは目標馬場状態を供給できず現状は実効化できない（詳細は 0006 ドキュメント参照）。
  # tipster 側は target_course_condition が None のとき本ボーナスをスキップする実装にしてある。
  course_condition_match_bonus: float = 1.0


def load_params_from_yaml(path: str) -> ScoringParams:
  """YAML ファイルから ScoringParams を読み込む。

  YAML には ScoringParams のフィールド名をキーとした dict を記述する。
  記載のないフィールドはデフォルト値が使われる（部分的な上書きが可能）。
  未知のキーは ML 最適化中の typo 等を早期検出するため例外を送出する。
  """
  import yaml

  with open(path, encoding='utf-8') as f:
    data = yaml.safe_load(f) or {}

  if not isinstance(data, dict):
    raise ValueError(f'YAML のトップレベルは dict である必要があります: {path}')

  valid_keys = {field.name for field in fields(ScoringParams)}
  unknown_keys = set(data.keys()) - valid_keys
  if unknown_keys:
    raise ValueError(f'未知のパラメータが含まれています: {sorted(unknown_keys)}')

  return ScoringParams(**data)


def dump_params_to_yaml(params: ScoringParams, path: str,
                        metrics: Optional[dict] = None) -> None:
  """ScoringParams を YAML ファイルに書き出す（最適化結果の保存等に使う）。

  metrics を渡すと、その各項目（tansho_hit や top3 など）を YAML 先頭の
  コメント行として埋め込む。コメントは YAML の読み込み時には無視されるため、
  load_params_from_yaml での再読み込みには影響しない。
  """
  import yaml

  with open(path, 'w', encoding='utf-8') as f:
    if metrics:
      f.write('# === 最適化時の評価指標 ===\n')
      for key, value in metrics.items():
        if isinstance(value, float):
          f.write(f'# {key}: {value:.6f}\n')
        else:
          f.write(f'# {key}: {value}\n')
    yaml.safe_dump(asdict(params), f, allow_unicode=True, sort_keys=False)
