# coding: utf-8
"""
過学習防止のためのホールドアウト検証（Phase 4 タスク17・リスク5）

27レースを訓練/検証に分割し、訓練データのみで Optuna 最適化 → 得た best params を
検証データで評価する。訓練 top3 と検証 top3 がそれぞれのベースラインを上回るか、
乖離が大きくないか（過学習の兆候 = train↑ だが val↓）を確認する。

母数27と少ないため、任意で k-fold（デフォルト3-fold）でも頑健性を見る。

分割は固定 seed のシャッフルで決定論的。データリーク防止は run_backtest と同一実装。
最適化（fixtures キャッシュ・複合スコア）は optimize.py を再利用する。
"""
import argparse
import os
import random
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraping_common import load_backtest_targets  # noqa: E402
from run_backtest import build_fixture_cache, evaluate_params  # noqa: E402
from scoring_params import ScoringParams  # noqa: E402
from optimize import (  # noqa: E402
    run_study,
    suggest_params,
    summarize_best,
    composite_score,
    RANDOM_SEED,
)

# 分割用の固定 seed（最適化サンプラーの seed とは独立に固定）。
SPLIT_SEED: int = 7


def split_train_val(fixture_cache: List[dict], val_ratio: float, seed: int) -> Tuple[List[dict], List[dict]]:
  """fixture_cache を訓練/検証に決定論的に分割する（シャッフル後に先頭を検証へ）。"""
  shuffled = list(fixture_cache)
  random.Random(seed).shuffle(shuffled)
  val_size = max(1, round(len(shuffled) * val_ratio))
  val = shuffled[:val_size]
  train = shuffled[val_size:]
  return train, val


def _print_summary(label: str, summary: dict) -> None:
  print(f'  [{label}] レース数={summary["race_count"]}  '
        f'top3={summary["average_top3_hit_count"]:.4f}  '
        f'spearman={summary["average_spearman"]:.4f}  '
        f'tansho={summary["average_tansho_hit"]:.4f}')


def holdout(fixture_cache: List[dict], n_trials: int, val_ratio: float,
            split_seed: int, opt_seed: int) -> None:
  """単一の train/val ホールドアウト検証を実行して結果を出力する。"""
  train, val = split_train_val(fixture_cache, val_ratio, split_seed)
  print(f'=== ホールドアウト検証（train={len(train)} / val={len(val)}, '
        f'split_seed={split_seed}, n_trials={n_trials}） ===')

  default_params = ScoringParams()
  train_baseline = evaluate_params(default_params, train)
  val_baseline = evaluate_params(default_params, val)
  print('--- ベースライン（デフォルト ScoringParams） ---')
  _print_summary('train baseline', train_baseline)
  _print_summary('val   baseline', val_baseline)

  # 訓練データのみで最適化する（検証データは一切触れない＝リークなし）。
  study = run_study(train, n_trials=n_trials, seed=opt_seed)
  best_params = suggest_params(study.best_trial)

  train_best = evaluate_params(best_params, train)
  val_best = evaluate_params(best_params, val)
  print('--- 最適化後（train で最適化した best params） ---')
  _print_summary('train best', train_best)
  _print_summary('val   best', val_best)

  train_gain = train_best['average_top3_hit_count'] - train_baseline['average_top3_hit_count']
  val_gain = val_best['average_top3_hit_count'] - val_baseline['average_top3_hit_count']
  print('--- top3 改善幅（best - baseline） ---')
  print(f'  train: {train_gain:+.4f}   val: {val_gain:+.4f}')
  print('--- 過学習評価 ---')
  if val_gain > 0 and train_gain > 0:
    print('  訓練・検証とも top3 がベースラインを上回った（過学習の兆候は弱い）。')
  elif train_gain > 0 and val_gain <= 0:
    print('  訓練のみ改善・検証は非改善 → 過学習の兆候あり。探索範囲縮小・パラメータ削減を検討。')
  else:
    print('  訓練でも改善が小さい/出ない → 探索範囲やデータを見直す必要。')


def kfold(fixture_cache: List[dict], n_trials: int, k: int,
          split_seed: int, opt_seed: int) -> None:
  """k-fold で頑健性を見る。各 fold を検証、残りを訓練として最適化→検証評価。"""
  shuffled = list(fixture_cache)
  random.Random(split_seed).shuffle(shuffled)
  folds = [shuffled[i::k] for i in range(k)]  # ラウンドロビン分割（各 fold のサイズを均等化）

  print(f'=== k-fold 検証（k={k}, n_trials={n_trials}/fold） ===')
  default_params = ScoringParams()
  val_gains: List[float] = []
  for fold_idx in range(k):
    val = folds[fold_idx]
    train = [e for i, f in enumerate(folds) if i != fold_idx for e in f]

    train_baseline = evaluate_params(default_params, train)
    val_baseline = evaluate_params(default_params, val)

    study = run_study(train, n_trials=n_trials, seed=opt_seed)
    best_params = suggest_params(study.best_trial)
    train_best = evaluate_params(best_params, train)
    val_best = evaluate_params(best_params, val)

    train_gain = train_best['average_top3_hit_count'] - train_baseline['average_top3_hit_count']
    val_gain = val_best['average_top3_hit_count'] - val_baseline['average_top3_hit_count']
    val_gains.append(val_gain)
    print(f'  fold{fold_idx + 1} (train={len(train)}/val={len(val)}): '
          f'train top3 {train_baseline["average_top3_hit_count"]:.3f}→{train_best["average_top3_hit_count"]:.3f} '
          f'({train_gain:+.3f}) | '
          f'val top3 {val_baseline["average_top3_hit_count"]:.3f}→{val_best["average_top3_hit_count"]:.3f} '
          f'({val_gain:+.3f})')

  mean_val_gain = sum(val_gains) / len(val_gains)
  positive_folds = sum(1 for g in val_gains if g > 0)
  print(f'--- k-fold まとめ: 検証 top3 改善 平均={mean_val_gain:+.4f}, '
        f'改善 fold={positive_folds}/{k} ---')


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(description='ホールドアウト/k-fold 検証（Phase 4 タスク17）')
  parser.add_argument('--n-trials', type=int, default=200, help='最適化の試行回数')
  parser.add_argument('--val-ratio', type=float, default=0.3,
                      help='検証データの比率（デフォルト0.3 ≈ 8/27）')
  parser.add_argument('--split-seed', type=int, default=SPLIT_SEED, help='分割の固定 seed')
  parser.add_argument('--opt-seed', type=int, default=RANDOM_SEED, help='最適化サンプラーの seed')
  parser.add_argument('--kfold', type=int, default=0,
                      help='>0 なら k-fold 検証も実施（例 3）')
  return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
  args = _parse_args(argv)
  import optuna
  optuna.logging.set_verbosity(optuna.logging.WARNING)

  targets = load_backtest_targets()
  fixture_cache = build_fixture_cache(targets)
  print(f'対象レース数（fixtures 読み込み成功）: {len(fixture_cache)}')

  holdout(fixture_cache, n_trials=args.n_trials, val_ratio=args.val_ratio,
          split_seed=args.split_seed, opt_seed=args.opt_seed)

  if args.kfold > 0:
    print()
    kfold(fixture_cache, n_trials=args.n_trials, k=args.kfold,
          split_seed=args.split_seed, opt_seed=args.opt_seed)


if __name__ == '__main__':
  main()
