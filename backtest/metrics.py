# coding: utf-8
"""
バックテスト評価指標（純Python実装）

numpy / scipy は環境に存在しないため、標準ライブラリのみで実装する。
予想順位（predicted_order）と実着順（actual_order）はいずれも
「馬名のリスト」で受け渡す前提とする。

- predicted_order: スコア降順に並べた馬名リスト（先頭がスコア1位）
- actual_order:    実着順に並べた馬名リスト（先頭が実1着）
"""
from statistics import mean
from typing import List


# 3着内的中の判定で参照する上位頭数（単勝1着〜複勝3着の範囲）
TOP_N_FOR_HIT: int = 3


def tansho_hit(predicted_order: List[str], actual_order: List[str]) -> float:
  """スコア1位の馬が実際の1着なら 1.0、違えば 0.0 を返す（単勝的中）。"""
  if not predicted_order or not actual_order:
    return 0.0
  return 1.0 if predicted_order[0] == actual_order[0] else 0.0


def top3_hit_count(predicted_order: List[str], actual_order: List[str]) -> int:
  """スコア上位3頭のうち、実際の1〜3着馬が何頭含まれるかを返す（0〜3）。"""
  predicted_top3 = set(predicted_order[:TOP_N_FOR_HIT])
  actual_top3 = set(actual_order[:TOP_N_FOR_HIT])
  return len(predicted_top3 & actual_top3)


def _to_average_ranks(values: List[float]) -> List[float]:
  """
  値リストを「昇順での順位」に変換する。同値（タイ）は平均順位を割り当てる。

  例: values=[10, 20, 20, 30] -> ranks=[1.0, 2.5, 2.5, 4.0]
  スピアマン相関でタイを扱うための前処理。
  """
  # (値, 元インデックス) を昇順ソートし、同値グループに平均順位を配る
  indexed_sorted = sorted(range(len(values)), key=lambda i: values[i])
  ranks: List[float] = [0.0] * len(values)

  group_start = 0
  while group_start < len(indexed_sorted):
    group_end = group_start
    # 同値が続く範囲（タイのグループ）を見つける
    while (group_end + 1 < len(indexed_sorted)
           and values[indexed_sorted[group_end + 1]] == values[indexed_sorted[group_start]]):
      group_end += 1
    # 順位は1始まり。グループ内の平均順位を全メンバーに割り当てる
    average_rank = (group_start + group_end) / 2 + 1
    for pos in range(group_start, group_end + 1):
      ranks[indexed_sorted[pos]] = average_rank
    group_start = group_end + 1

  return ranks


def spearman(predicted_ranks: List[float], actual_ranks: List[float]) -> float:
  """
  スピアマン順位相関係数（-1〜1）を純Pythonで計算する。

  predicted_ranks / actual_ranks は同じ馬を同じ要素位置に並べた「順位（または順位の根拠となる値）」。
  ピアソン相関を順位列に対して適用する一般式で実装し、タイは平均順位で扱う。
  値が定数（分散ゼロ）の場合は相関を定義できないため 0.0 を返す。
  """
  if len(predicted_ranks) != len(actual_ranks):
    raise ValueError('predicted_ranks と actual_ranks の長さが一致しません')
  num = len(predicted_ranks)
  if num < 2:
    return 0.0

  rank_x = _to_average_ranks(predicted_ranks)
  rank_y = _to_average_ranks(actual_ranks)

  mean_x = mean(rank_x)
  mean_y = mean(rank_y)

  covariance = sum((rx - mean_x) * (ry - mean_y) for rx, ry in zip(rank_x, rank_y))
  variance_x = sum((rx - mean_x) ** 2 for rx in rank_x)
  variance_y = sum((ry - mean_y) ** 2 for ry in rank_y)

  if variance_x == 0 or variance_y == 0:
    return 0.0

  return covariance / (variance_x ** 0.5 * variance_y ** 0.5)


def spearman_from_orders(predicted_order: List[str], actual_order: List[str]) -> float:
  """
  馬名の予想順位リストと実着順リストからスピアマン相関を計算する。

  両リストに共通して含まれる馬のみを対象とする（一方にしかいない馬は照合不能のため除外）。
  各馬について「予想順位」「実着順」を順位値として相関を取る。
  """
  predicted_rank_by_name = {name: rank for rank, name in enumerate(predicted_order, start=1)}
  actual_rank_by_name = {name: rank for rank, name in enumerate(actual_order, start=1)}

  common_names = [name for name in predicted_order if name in actual_rank_by_name]
  if len(common_names) < 2:
    return 0.0

  predicted_ranks = [predicted_rank_by_name[name] for name in common_names]
  actual_ranks = [actual_rank_by_name[name] for name in common_names]
  return spearman(predicted_ranks, actual_ranks)


def aggregate(per_race_metrics: List[dict]) -> dict:
  """
  複数レースの指標を集計し、平均値を返す。

  per_race_metrics の各要素は以下のキーを持つ dict:
    - tansho_hit: float
    - top3_hit_count: int
    - spearman: float
  """
  if not per_race_metrics:
    return {
      'race_count': 0,
      'average_tansho_hit': 0.0,
      'average_top3_hit_count': 0.0,
      'average_spearman': 0.0,
    }

  return {
    'race_count': len(per_race_metrics),
    'average_tansho_hit': mean(m['tansho_hit'] for m in per_race_metrics),
    'average_top3_hit_count': mean(m['top3_hit_count'] for m in per_race_metrics),
    'average_spearman': mean(m['spearman'] for m in per_race_metrics),
  }
