# coding: utf-8
"""
バックテスト実行スクリプト

処理フロー（全行程ネットワーク不要）:
  1. race-info/ の yml を読み込む
  2. fixtures/<race_id>/horses.json（オフラインキャッシュ）から出走馬と戦績を復元する
  3. 【データリーク防止】対象レース日（ground_truth の race_date）より前の戦績のみに絞る
  4. Tipster().execute() でスコアを付与する
  5. スコア降順の予想順位を作り、ground_truth の実着順と馬名で照合する
  6. metrics.py で評価指標（単勝的中・3着内的中・スピアマン相関）を計算する
  7. 結果を results/baseline_<識別子>.json に保存し、サマリを標準出力する
"""
import argparse
import json
import os
import sys
from typing import Final, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scraping_common import Horse, RaceResult, load_backtest_targets  # noqa: E402
import metrics  # noqa: E402

# tipster は special-week/ 配下。scraping_common 経由でパスが通っている
from tipster import Tipster  # noqa: E402
from scoring_params import ScoringParams, load_params_from_yaml  # noqa: E402

_BACKTEST_DIR: Final[str] = os.path.dirname(os.path.abspath(__file__))
FIXTURES_DIR: Final[str] = os.path.join(_BACKTEST_DIR, 'fixtures')
GROUND_TRUTH_DIR: Final[str] = os.path.join(_BACKTEST_DIR, 'ground_truth')
RESULTS_DIR: Final[str] = os.path.join(_BACKTEST_DIR, 'results')

# 結果ファイル名は再現性のため固定文字列（タイムスタンプを使わない）。
# レース拡充（27レース）後の新ベースラインは run_id='expanded' とし、
# 引数なし実行は新ベースライン results/baseline_expanded.json を生成する。
# Phase1/2 の旧5レースベースライン results/baseline_baseline.json は基準値として温存し、
# 明示的に --run-id baseline を指定したときだけ上書きされる（通常は触らない）。
DEFAULT_RUN_ID: Final[str] = 'expanded'

# 「ベースライン」を表す run_id（results/baseline_<id>.json に保存）。
# それ以外は実験扱いで results/phase3_<id>.json に保存しベースラインを汚さない。
#   baseline    : Phase1/2 の旧5レース基準値（温存・上書き禁止）
#   expanded    : Phase4 の27レース新ベースライン（温存・上書き禁止）
#   expanded_v2 : 本フェーズの46レース新ベースライン（汎化改善のための母数拡充）
_BASELINE_RUN_IDS: Final = frozenset({'baseline', 'expanded', 'expanded_v2'})


def _normalize_race_date_to_ymd(race_date: str) -> Optional[str]:
  """ground_truth の race_date（YYYYMMDD）を比較用の YYYY/MM/DD に整形する。"""
  if not race_date or len(race_date) != 8 or not race_date.isdigit():
    return None
  return f'{race_date[0:4]}/{race_date[4:6]}/{race_date[6:8]}'


def _restore_race_results(result_dicts: List[dict], race_date_ymd: Optional[str]) -> List[RaceResult]:
  """
  fixtures の戦績 dict 群を RaceResult に復元する。

  データリーク防止: race_date_ymd（対象レース日）以降の戦績を除外する。
  戦績の日付（RaceResult.date）は 'YYYY/MM/DD' 形式。文字列比較で日付の大小を判定できる。
  対象レース当日（=同日）の戦績も「そのレース自身またはリーク」とみなし除外する。
  """
  race_results: List[RaceResult] = []
  for d in result_dicts:
    if race_date_ymd is not None and d['date'] and d['date'] >= race_date_ymd:
      continue  # 対象レース日以降の戦績はリークになるため使わない
    race_results.append(RaceResult(
      date=d['date'],
      course=d['course'],
      weather=d['weather'],
      race_name=d['race_name'],
      race_grade=d['race_grade'],
      number_of_horses=d['number_of_horses'],
      popularity=d['popularity'],
      ranking=d['ranking'],
      distance=d['distance'],
      course_type=d['course_type'],
      course_condition=d['course_condition'],
      time=d['time'],
      difference=d['difference'],
      passing=d['passing'],
    ))
  return race_results


def _restore_horses(horse_dicts: List[dict], race_date_ymd: Optional[str]) -> List[Horse]:
  horses: List[Horse] = []
  for h in horse_dicts:
    race_results = _restore_race_results(h['race_results'], race_date_ymd)
    horses.append(Horse(
      pos=h['pos'],
      horse_name=h['horse_name'],
      sex=h['sex'],
      age=h['age'],
      additional_weight=h['additional_weight'],
      race_results=race_results,
    ))
  return horses


def _load_json(path: str) -> Optional[dict]:
  if not os.path.exists(path):
    return None
  with open(path, encoding='utf-8') as f:
    return json.load(f)


def _build_actual_order(ground_truth: dict) -> List[str]:
  """ground_truth から実着順（ranking 昇順）の馬名リストを作る。"""
  sorted_results = sorted(ground_truth['results'], key=lambda r: r['ranking'])
  return [r['horse_name'] for r in sorted_results]


def load_race_fixture(race_id: str) -> Optional[dict]:
  """1レース分の fixtures（出走馬の戦績）と ground_truth をディスクから読み込む。

  Phase 4 の Optuna 最適化では同じ fixtures を数百回読み直すと遅いため、
  この関数の戻り値を呼び出し側でメモリにキャッシュして使い回す（後述 build_fixture_cache）。
  読み込めない場合は None を返す。
  """
  fixture_path = os.path.join(FIXTURES_DIR, race_id, 'horses.json')
  ground_truth_path = os.path.join(GROUND_TRUTH_DIR, f'{race_id}.json')

  horse_dicts = _load_json(fixture_path)
  ground_truth = _load_json(ground_truth_path)
  if horse_dicts is None:
    print(f'  [SKIP] fixtures がありません: {fixture_path}')
    return None
  if ground_truth is None:
    print(f'  [SKIP] ground_truth がありません: {ground_truth_path}')
    return None
  return {'horse_dicts': horse_dicts, 'ground_truth': ground_truth}


def run_one_race_from_fixture(race_info: dict, fixture: dict, tipster: Tipster) -> dict:
  """読み込み済みの fixture（horse_dicts / ground_truth）から1レースを採点・評価する。

  ディスクI/Oを含まないため、Optuna の試行ループから高速に繰り返し呼べる。
  データリーク防止（対象レース日以降の戦績の除外）は run_one_race と同一ロジック。
  """
  horse_dicts = fixture['horse_dicts']
  ground_truth = fixture['ground_truth']

  race_date_ymd = _normalize_race_date_to_ymd(ground_truth.get('race_date'))
  horses = _restore_horses(horse_dicts, race_date_ymd)
  tipster.execute(race_info, horses)

  horses_sorted = sorted(horses, key=lambda h: h.score, reverse=True)
  predicted_order = [h.horse_name for h in horses_sorted]
  actual_order = _build_actual_order(ground_truth)

  return {
    'tansho_hit': metrics.tansho_hit(predicted_order, actual_order),
    'top3_hit_count': metrics.top3_hit_count(predicted_order, actual_order),
    'spearman': metrics.spearman_from_orders(predicted_order, actual_order),
  }


def run_one_race(race_id: str, race_info: dict, tipster: Tipster) -> Optional[dict]:
  """1レースのバックテストを実行し、指標と対比を含む dict を返す。不能時は None。"""
  fixture = load_race_fixture(race_id)
  if fixture is None:
    return None
  horse_dicts = fixture['horse_dicts']
  ground_truth = fixture['ground_truth']

  race_date_ymd = _normalize_race_date_to_ymd(ground_truth.get('race_date'))
  if race_date_ymd is None:
    print(f'  [WARN] 開催日不明のためデータリークフィルタを適用しません（全戦績使用）')

  horses = _restore_horses(horse_dicts, race_date_ymd)
  tipster.execute(race_info, horses)

  horses_sorted = sorted(horses, key=lambda h: h.score, reverse=True)
  predicted_order = [h.horse_name for h in horses_sorted]
  actual_order = _build_actual_order(ground_truth)

  per_race = {
    'tansho_hit': metrics.tansho_hit(predicted_order, actual_order),
    'top3_hit_count': metrics.top3_hit_count(predicted_order, actual_order),
    'spearman': metrics.spearman_from_orders(predicted_order, actual_order),
  }

  # 予想順位 vs 実着順の対比（馬名で照合）
  actual_rank_by_name = {r['horse_name']: r['ranking'] for r in ground_truth['results']}
  comparison = []
  for predicted_rank, horse in enumerate(horses_sorted, start=1):
    comparison.append({
      'horse_name': horse.horse_name,
      'predicted_rank': predicted_rank,
      'score': round(horse.score, 4),
      'actual_rank': actual_rank_by_name.get(horse.horse_name),
      'used_results_count': len(horse.race_results),
    })

  return {
    'race_id': race_id,
    'race_name': race_info.get('race_name'),
    'race_date': ground_truth.get('race_date'),
    'leak_filter_applied': race_date_ymd is not None,
    'metrics': per_race,
    'comparison': comparison,
  }


def build_fixture_cache(targets: List) -> List[dict]:
  """対象レース全件の fixtures / ground_truth を一度だけ読み込んでキャッシュ化する。

  Optuna の最適化では同じ27レースを試行回数ぶん採点する。毎試行でディスクから
  読み直すと無駄なので、ここで (race_info, fixture) を1回だけ読み込み、
  以降は run_one_race_from_fixture にメモリ上のデータを渡して使い回す。

  戻り値は読み込みに成功したレースのみの
  [{'race_id', 'race_info', 'fixture'}] リスト。
  """
  cache: List[dict] = []
  for _file_name, race_id, race_info in targets:
    fixture = load_race_fixture(race_id)
    if fixture is None:
      continue
    cache.append({'race_id': race_id, 'race_info': race_info, 'fixture': fixture})
  return cache


def evaluate_params(params: ScoringParams, fixture_cache: List[dict]) -> dict:
  """ScoringParams を1セット受け取り、キャッシュ済みレース群を採点して集計指標を返す。

  返す dict は metrics.aggregate と同形（race_count / average_tansho_hit /
  average_top3_hit_count / average_spearman）。Optuna の目的関数から呼ぶ想定。
  """
  tipster = Tipster(params)
  per_race_metrics = [
    run_one_race_from_fixture(entry['race_info'], entry['fixture'], tipster)
    for entry in fixture_cache
  ]
  return metrics.aggregate(per_race_metrics)


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description='バックテスト実行（オフライン fixtures で完結）',
  )
  parser.add_argument(
    '--params',
    metavar='YAML',
    default=None,
    help='ScoringParams を記述した YAML へのパス。未指定ならデフォルト ScoringParams（ベースライン）を使う。',
  )
  parser.add_argument(
    '--run-id',
    metavar='ID',
    default=DEFAULT_RUN_ID,
    help=('結果ファイル名と出力 run_id に使う識別子（デフォルト expanded）。'
          'baseline / expanded は results/baseline_<ID>.json、'
          'それ以外は実験として results/phase3_<ID>.json に保存する。'),
  )
  return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None):
  args = _parse_args(argv)
  os.makedirs(RESULTS_DIR, exist_ok=True)

  # --params 未指定なら従来どおりデフォルト ScoringParams（ベースライン）で実行する（後方互換）。
  if args.params is not None:
    params = load_params_from_yaml(args.params)
    print(f'ScoringParams を読み込みました: {args.params}')
  else:
    params = ScoringParams()
  tipster = Tipster(params)

  run_id = args.run_id
  # race-info/*.yml と race_manifest.json をマージした全対象を回す（引数なしで全レース）。
  targets = load_backtest_targets()

  per_race_results = []
  per_race_metrics = []
  for file_name, race_id, race_info in targets:
    print(f'=== {file_name} (race_id={race_id}) ===')
    race_result = run_one_race(race_id, race_info, tipster)
    if race_result is None:
      continue
    per_race_results.append(race_result)
    per_race_metrics.append(race_result['metrics'])
    m = race_result['metrics']
    print(f'  単勝的中={m["tansho_hit"]:.0f}  3着内的中頭数={m["top3_hit_count"]}  '
          f'スピアマン相関={m["spearman"]:.4f}  リークフィルタ={race_result["leak_filter_applied"]}')

  summary = metrics.aggregate(per_race_metrics)

  output = {
    'run_id': run_id,
    'summary': summary,
    'races': per_race_results,
  }
  # ベースライン run_id（baseline / expanded）は baseline_<run_id>.json に保存する。
  # それ以外（実験）は phase3_<run_id>.json に保存してベースライン結果を上書きしない。
  if run_id in _BASELINE_RUN_IDS:
    output_path = os.path.join(RESULTS_DIR, f'baseline_{run_id}.json')
  else:
    output_path = os.path.join(RESULTS_DIR, f'phase3_{run_id}.json')
  with open(output_path, 'w', encoding='utf-8') as f:
    json.dump(output, f, ensure_ascii=False, indent=2)

  print()
  print('=== ベースライン指標サマリ ===')
  print(f'  対象レース数        : {summary["race_count"]}')
  print(f'  平均単勝的中率      : {summary["average_tansho_hit"]:.4f}')
  print(f'  平均3着内的中頭数   : {summary["average_top3_hit_count"]:.4f} / 3')
  print(f'  平均スピアマン相関  : {summary["average_spearman"]:.4f}')
  print()
  print(f'結果を保存しました: {output_path}')


if __name__ == '__main__':
  main()
