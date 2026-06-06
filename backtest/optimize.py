# coding: utf-8
"""
Optuna による ScoringParams の自動最適化（Phase 4）

目的（最大化対象）:
  「平均3着内的中頭数（top3_hit_count）」と「平均単勝的中率（tansho_hit）」を
  同等に重視する（ユーザー決定）。両指標はスケールが異なる（top3 はレース毎 0〜3、
  単勝は 0〜1）ため、top3 を理論上限 3 で割って 0〜1 に正規化したうえで単勝と 1:1 で
  加算し、僅差の同点をスピアマン相関で割る「複合スコア」を Optuna の単一目的値として返す:

      composite = average_top3_hit_count / TOP3_MAX
                + average_tansho_hit
                + TIE_BREAK_EPSILON * average_spearman

  TIE_BREAK_EPSILON は十分小さく取り（0.001）、主指標（正規化 top3・単勝）の優劣が
  常にスピアマンの寄与より優先される。スピアマンは [-1, 1] なので寄与は最大でも ±0.001。
  一方、主指標の最小有意差は「1頭の入れ替え＝1/レース数」で、正規化 top3 なら
  1/(3×レース数)。27レースなら約 0.0123、単勝なら約 0.037 で、いずれも 0.001 ≪ なので、
  主指標が少しでも勝るパラメータは composite でも必ず勝つ。主指標が完全同点のときだけ
  スピアマンがタイブレークとして効く。
  → 主目的=正規化 top3＋単勝、副目的=スピアマンの「辞書式順序」を単一スカラーで表現している。

設計:
  - run_backtest.py の評価ロジックを import 再利用する（サブプロセスを起動しない）。
    fixtures はメモリにキャッシュ（build_fixture_cache）し、毎試行の再読込を避ける。
  - データリーク防止（対象レース日以降の戦績の除外）は run_backtest と同一実装。
  - サンプラーは TPESampler(seed=42) で固定し、同一 seed で再現可能。

探索範囲・制約はモジュール冒頭の定数群とコメントを参照。
"""
import argparse
import os
import sys
import time
from dataclasses import asdict
from typing import Dict, List, Optional

import optuna

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# scraping_common を先に import すると special-week/ 配下（scoring_params 等）へ
# sys.path が通る。これより前に scoring_params を import するとパスが無く失敗する。
from scraping_common import load_backtest_targets  # noqa: E402
from run_backtest import build_fixture_cache, evaluate_params  # noqa: E402
from scoring_params import ScoringParams, dump_params_to_yaml  # noqa: E402

_BACKTEST_DIR = os.path.dirname(os.path.abspath(__file__))
BEST_PARAMS_PATH = os.path.join(_BACKTEST_DIR, 'best_params.yaml')

# 複合スコアのタイブレーク係数（上記モジュール docstring 参照）。
# 主指標の最小有意差（正規化 top3 で 1/(3×レース数) ≈ 0.0123 @27レース）より十分小さく、
# スピアマン（[-1,1]）の寄与が主指標の優劣を覆さない値にしている。
TIE_BREAK_EPSILON: float = 0.001

# top3_hit_count の理論上限（スコア上位3頭中の3着内的中頭数の最大）。
# 単勝的中率（0〜1）とスケールを揃えるため、これで割って 0〜1 に正規化する。
TOP3_MAX: float = 3.0

# 再現性のための固定 seed（サンプラーに与える）。
RANDOM_SEED: int = 42


def composite_score(summary: dict) -> float:
  """集計指標 dict から複合スコア（正規化 top3＋単勝が主・スピアマン従）を計算する。"""
  normalized_top3 = summary['average_top3_hit_count'] / TOP3_MAX
  return (normalized_top3
          + summary['average_tansho_hit']
          + TIE_BREAK_EPSILON * summary['average_spearman'])


def suggest_params(trial: optuna.Trial) -> ScoringParams:
  """Optuna の trial から ScoringParams を1セットサンプリングする。

  --- 探索範囲（プラン0001 タスク15の初期値を踏襲） ---
    勝利加点系          : [0.5, 15.0]
    重賞加点系          : [0, 50.0]（順序制約は下記の積み上げ方式で保証）
    着差係数            : [0.0, 3.0]
    距離基底            : [300, 3000]
    減点除数            : [1.0, 5.0]
    Phase 3 追加        : enable_field_size_correction(on/off) / field_size_base[12,18]
                          course_type_mismatch_penalty[0.3, 1.0]

  --- 重賞加点の順序制約（G1 ≥ G2 ≥ G3 かつ 同一グレード内 rank1 ≥ rank3 ≥ rank5） ---
    「差分を正の量としてサンプリングし積み上げる」方式で構造的に制約を保証する
    （事後ペナルティや並べ替えではなく、サンプリング時点で必ず順序が成立する）。

    各グレードについて、まず rank5（5着内加点）を基準値として [0, 50] からサンプリングし、
    rank3 = rank5 + (3着内の上乗せ ≥ 0)、rank1 = rank3 + (1着の上乗せ ≥ 0) と積み上げる。
    → 同一グレード内で rank1 ≥ rank3 ≥ rank5 が必ず成立。

    グレード間は g1_base ≥ g2_base ≥ g3_base を同様の積み上げで保証し、各ランク帯の
    上乗せも g1 ≥ g2 ≥ g3 となるよう「グレードが下がるほど上限を縮める」のではなく、
    g3 を基準にグレード差分（≥0）を積み上げる方式にする。これにより
    任意のランク帯 r で grade1_r ≥ grade2_r ≥ grade3_r も同時に成立する。

    積み上げで作る値が探索上限 50 を超えないよう、各差分の上限を配分してある。
  """
  # ---- 勝利加点系 [0.5, 15.0] ----
  win_score_top3 = trial.suggest_float('win_score_top3', 0.5, 15.0)
  # 1着加点は 2-3着加点以上にしたい（1着の方が価値が高いのは自明）。差分を積み上げて保証。
  win_score_1st = win_score_top3 + trial.suggest_float('win_score_1st_delta', 0.0, 15.0)

  # ---- 重賞加点系 [0, 50.0]（順序制約つき積み上げ） ----
  # グレード間差分の予算配分: rank5 基準 [0,20]、グレード差分 [0,10]×2 で g1_rank5 最大40。
  # ランク帯上乗せ: rank3 差分・rank1 差分をそれぞれ抑えめに取り、積み上げ後も 50 以内に収める。
  # 5着内加点（rank5）の基準は最下位グレード g3 を起点に積み上げる。
  g3_rank5 = trial.suggest_float('g3_rank5', 0.0, 20.0)
  g2_rank5 = g3_rank5 + trial.suggest_float('g2_rank5_delta', 0.0, 10.0)
  g1_rank5 = g2_rank5 + trial.suggest_float('g1_rank5_delta', 0.0, 10.0)

  # rank3（3着内加点）= rank5 + 各グレードの「3着内上乗せ」。
  # 上乗せもグレード順 g1 ≥ g2 ≥ g3 を保つよう、上乗せ自体を g3 起点で積み上げる。
  g3_rank3_extra = trial.suggest_float('g3_rank3_extra', 0.0, 10.0)
  g2_rank3_extra = g3_rank3_extra + trial.suggest_float('g2_rank3_extra_delta', 0.0, 5.0)
  g1_rank3_extra = g2_rank3_extra + trial.suggest_float('g1_rank3_extra_delta', 0.0, 5.0)
  g3_rank3 = g3_rank5 + g3_rank3_extra
  g2_rank3 = g2_rank5 + g2_rank3_extra
  g1_rank3 = g1_rank5 + g1_rank3_extra

  # rank1（1着加点）= rank3 + 各グレードの「1着上乗せ」。同様にグレード順を保つ。
  g3_rank1_extra = trial.suggest_float('g3_rank1_extra', 0.0, 10.0)
  g2_rank1_extra = g3_rank1_extra + trial.suggest_float('g2_rank1_extra_delta', 0.0, 5.0)
  g1_rank1_extra = g2_rank1_extra + trial.suggest_float('g1_rank1_extra_delta', 0.0, 5.0)
  g3_rank1 = g3_rank3 + g3_rank1_extra
  g2_rank1 = g2_rank3 + g2_rank1_extra
  g1_rank1 = g1_rank3 + g1_rank1_extra

  # ---- 着差係数 [0.0, 3.0] ----
  difference_multiplier = trial.suggest_float('difference_multiplier', 0.0, 3.0)

  # ---- 距離基底 [300, 3000] ----
  distance_base = trial.suggest_float('distance_base', 300.0, 3000.0)

  # ---- 負け減点 除数 [1.0, 5.0]（1.0=減点なし〜強ペナルティ） ----
  penalty_rank6plus = trial.suggest_float('penalty_rank6plus', 1.0, 5.0)
  penalty_rank4_5 = trial.suggest_float('penalty_rank4_5', 1.0, 5.0)
  penalty_rank2_3 = trial.suggest_float('penalty_rank2_3', 1.0, 5.0)

  # ---- Phase 3 追加パラメータ ----
  # 出走頭数補正: on/off を categorical で探索。base は 12〜18。
  enable_field_size_correction = trial.suggest_categorical(
    'enable_field_size_correction', [False, True])
  field_size_base = trial.suggest_int('field_size_base', 12, 18)
  # コース種別不一致ペナルティ: 0.3〜1.0（1.0=ペナルティなし）。
  course_type_mismatch_penalty = trial.suggest_float('course_type_mismatch_penalty', 0.3, 1.0)
  # 直近性減衰: 0.7〜1.0（1.0=減衰なし）。古い戦績ほど加点を緩やかに割り引く。
  recency_decay = trial.suggest_float('recency_decay', 0.8, 0.95)

  return ScoringParams(
    win_score_1st=win_score_1st,
    win_score_top3=win_score_top3,
    grade1_rank1=g1_rank1,
    grade1_rank3=g1_rank3,
    grade1_rank5=g1_rank5,
    grade2_rank1=g2_rank1,
    grade2_rank3=g2_rank3,
    grade2_rank5=g2_rank5,
    grade3_rank1=g3_rank1,
    grade3_rank3=g3_rank3,
    grade3_rank5=g3_rank5,
    difference_multiplier=difference_multiplier,
    distance_base=distance_base,
    penalty_rank6plus=penalty_rank6plus,
    penalty_rank4_5=penalty_rank4_5,
    penalty_rank2_3=penalty_rank2_3,
    enable_field_size_correction=enable_field_size_correction,
    field_size_base=field_size_base,
    course_type_mismatch_penalty=course_type_mismatch_penalty,
    recency_decay=recency_decay,
    # course_condition_match_bonus は対象当日馬場状態が供給できず実効化不能（0006）。
    # 探索しても neutral 以外は常にスキップされ無意味なので neutral 固定で除外する。
    course_condition_match_bonus=1.0,
    # 脚質閾値（run_type_*）はスコアに直接効かない（run_type はスコア未使用）ため
    # 探索対象外。デフォルト（neutral）のままにする。
  )


def _assert_grade_ordering(params: ScoringParams) -> None:
  """重賞加点の順序制約が保たれていることを検証する（防御的チェック）。"""
  assert params.grade1_rank5 >= params.grade2_rank5 >= params.grade3_rank5
  assert params.grade1_rank3 >= params.grade2_rank3 >= params.grade3_rank3
  assert params.grade1_rank1 >= params.grade2_rank1 >= params.grade3_rank1
  assert params.grade1_rank1 >= params.grade1_rank3 >= params.grade1_rank5
  assert params.grade2_rank1 >= params.grade2_rank3 >= params.grade2_rank5
  assert params.grade3_rank1 >= params.grade3_rank3 >= params.grade3_rank5


def make_objective(fixture_cache: List[dict]):
  """fixture_cache をクロージャに閉じ込んだ objective(trial) を返す。"""

  def objective(trial: optuna.Trial) -> float:
    params = suggest_params(trial)
    _assert_grade_ordering(params)  # 制約が崩れていないことを毎試行で確認
    summary = evaluate_params(params, fixture_cache)
    # 補助情報として top3 / spearman を trial に記録（分析用）。
    trial.set_user_attr('average_top3_hit_count', summary['average_top3_hit_count'])
    trial.set_user_attr('average_spearman', summary['average_spearman'])
    trial.set_user_attr('average_tansho_hit', summary['average_tansho_hit'])
    return composite_score(summary)

  return objective


def run_study(
    fixture_cache: List[dict],
    n_trials: int,
    seed: int = RANDOM_SEED,
    show_progress: bool = False,
) -> optuna.Study:
  """固定 seed の TPESampler でスタディを実行して返す（再現可能）。"""
  sampler = optuna.samplers.TPESampler(seed=seed)
  study = optuna.create_study(direction='maximize', sampler=sampler)
  study.optimize(make_objective(fixture_cache), n_trials=n_trials,
                 show_progress_bar=show_progress)
  return study


def summarize_best(study: optuna.Study) -> dict:
  """best trial の指標（複合スコア・top3・spearman・tansho）を取り出す。"""
  best = study.best_trial
  return {
    'composite': best.value,
    'average_top3_hit_count': best.user_attrs.get('average_top3_hit_count'),
    'average_spearman': best.user_attrs.get('average_spearman'),
    'average_tansho_hit': best.user_attrs.get('average_tansho_hit'),
  }


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(description='Optuna による ScoringParams 最適化（Phase 4）')
  parser.add_argument('--n-trials', type=int, default=200,
                      help='試行回数（デフォルト200）')
  parser.add_argument('--seed', type=int, default=None,
                      help='サンプラーの固定 seed（未指定時は現在時刻ミリ秒文字列を seed に使用）')
  parser.add_argument('--out', default=BEST_PARAMS_PATH,
                      help=f'最良パラメータの保存先 YAML（デフォルト {BEST_PARAMS_PATH}）')
  return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
  args = _parse_args(argv)
  # Optuna のログは試行ごとに大量に出るので警告以上に絞る。
  optuna.logging.set_verbosity(optuna.logging.WARNING)

  targets = load_backtest_targets()
  fixture_cache = build_fixture_cache(targets)
  print(f'対象レース数（fixtures 読み込み成功）: {len(fixture_cache)}')

  # ベースライン（デフォルト ScoringParams）の複合スコアを基準として算出する。
  baseline_summary = evaluate_params(ScoringParams(), fixture_cache)
  baseline_composite = composite_score(baseline_summary)
  print('=== ベースライン（デフォルト ScoringParams） ===')
  print(f'  平均3着内的中頭数 : {baseline_summary["average_top3_hit_count"]:.4f} / 3')
  print(f'  平均単勝的中率    : {baseline_summary["average_tansho_hit"]:.4f}')
  print(f'  平均スピアマン相関: {baseline_summary["average_spearman"]:.4f}')
  print(f'  複合スコア        : {baseline_composite:.6f}')

  # seed 未指定時は現在時刻ミリ秒文字列を seed として使う（毎回異なる探索になる）。
  # numpy/TPESampler の seed 上限（2**32 - 1）を超えないよう剰余で収める。
  seed = args.seed if args.seed is not None else int(str(int(time.time() * 1000))) % (2 ** 32)
  print(f'=== Optuna 最適化開始（n_trials={args.n_trials}, seed={seed}） ===')
  study = run_study(fixture_cache, n_trials=args.n_trials, seed=seed)

  best_metrics = summarize_best(study)
  best_params = suggest_params(study.best_trial)
  _assert_grade_ordering(best_params)

  print('=== 最良結果 ===')
  print(f'  平均3着内的中頭数 : {best_metrics["average_top3_hit_count"]:.4f} / 3')
  print(f'  平均単勝的中率    : {best_metrics["average_tansho_hit"]:.4f}')
  print(f'  平均スピアマン相関: {best_metrics["average_spearman"]:.4f}')
  print(f'  複合スコア        : {best_metrics["composite"]:.6f}')
  print(f'  ベースライン比改善: {best_metrics["composite"] - baseline_composite:+.6f}')
  improved = best_metrics['composite'] > baseline_composite
  print(f'  ベースライン超え  : {improved}')

  dump_params_to_yaml(best_params, args.out, metrics=best_metrics)
  print(f'最良パラメータを保存しました: {args.out}')


if __name__ == '__main__':
  main()
