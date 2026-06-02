# special-week

競馬予想支援

netkeiba の戦績データをスクレイピングし、過去成績からスコアを付けて出走馬を順位付けする予想システム。
予想結果は Elasticsearch に投入して検索・確認できる。あわせて、スコアリングのパラメータを
バックテストで評価し、Optuna で自動最適化する仕組み（`backtest/`）を備える。

## Usage

`race-info/race-info.yml` を編集。IDは `https://race.netkeiba.com` のものを記載。

### Elasticsearch起動

以下コマンドを実行してインデックスが出来るまで待つ。 `http://localhost:9200/hoses/_search`

```
$ docker-compose build
$ docker-compose up
```

### Queryを実行
```
$ cd query
$ bash query-test.sh general.json
$ cat outputs/out-general.json
```

---

## ディレクトリ構成

| パス | 役割 |
|---|---|
| `special-week/special-week/` | 予想本体 |
| `special-week/special-week/special-week.py` | エントリポイント。出走表スクレイピング → スコアリング → ES 投入 |
| `special-week/special-week/tipster.py` | スコアリング本体（`Tipster`）。過去戦績から勝利加点・重賞加点・距離補正・凡走減点を計算 |
| `special-week/special-week/horse.py` | 馬・戦績（`Horse` / `RaceResult`）。脚質判定 `getRunType()` を含む |
| `special-week/special-week/scoring_params.py` | スコアリングの全パラメータを集約した `ScoringParams`（dataclass）＋ YAML 入出力 |
| `special-week/special-week/sp_es.py` | Elasticsearch 投入 |
| `special-week/backtest/` | 評価・最適化の基盤（オフライン・決定論的） |
| `special-week/backtest/metrics.py` | 評価指標（単勝的中・3着内的中頭数・スピアマン相関） |
| `special-week/backtest/scraping_common.py` | スクレイピング共通ブリッジ＋対象レースの共通ローダ `load_backtest_targets()` |
| `special-week/backtest/collect_ground_truth.py` | 正解ラベル（実着順）収集 |
| `special-week/backtest/collect_fixtures.py` | オフライン戦績キャッシュ収集（堅牢収集） |
| `special-week/backtest/build_race_manifest.py` | 開催日別一覧から重賞 race_id を発見し `race_manifest.json` を生成 |
| `special-week/backtest/run_backtest.py` | バックテスト実行（fixtures → Tipster → 照合 → 指標） |
| `special-week/backtest/optimize.py` | Optuna によるパラメータ最適化 |
| `special-week/backtest/holdout_validate.py` | ホールドアウト / k-fold による汎化検証 |
| `special-week/backtest/race_manifest.json` | バックテスト対象の重賞メタ（race_id→レース名/距離/コース種別/開催日 等） |
| `special-week/backtest/ground_truth/` | 各レースの正解ラベル（`<race_id>.json`） |
| `special-week/backtest/fixtures/` | 各レースの戦績キャッシュ（`<race_id>/horses.json`） |
| `special-week/backtest/results/` | バックテスト結果 JSON（ベースライン・実験結果） |
| `special-week/backtest/experiments/` | 個別パラメータを ON にした実験用 YAML |
| `special-week/backtest/best_params*.yaml` | 最適化結果のパラメータ（後述） |
| `special-week/backtest/baseline_params.yaml` | 最適化前のベースライン値（`ScoringParams` のデフォルトを最適化値にした際の退避先） |

---

## セットアップ（依存）

スクレイピング・予想本体は `beautifulsoup4` / `pyyaml` / `requests` で動く。
バックテストの最適化（`optimize.py`）には `optuna` / `numpy` が追加で必要。すべて `requirements.txt` に記載済み。

```
$ pip install -r special-week/requirements.txt
```

> バックテスト・最適化はオフラインの fixtures だけで完結し、決定論的に再実行できる
> （ネットワークは正解ラベル/戦績の「収集時」のみ必要）。

---

## バックテストと評価指標

### 評価指標の説明

`backtest/metrics.py` で以下の3指標を計算する。各指標は「予想順位（スコア降順の馬名リスト）」と
「実着順（馬名リスト）」を照合して求める。

| 指標 | 定義 | 見方 |
|---|---|---|
| 単勝的中率 (`tansho_hit`) | スコア1位の馬が実1着なら 1.0、違えば 0.0 | 1着をピンポイントで当てる力。最もシビア |
| 3着内的中頭数 (`top3_hit_count`) | スコア上位3頭のうち実1〜3着馬が何頭含まれるか（0〜3） | **本プロジェクトの主指標**。複勝圏をどれだけ拾えるか |
| スピアマン相関 (`spearman`) | スコア順位と実着順の順位相関（-1〜1） | 全体の並びの正しさ。1に近いほど良い。0付近は無相関 |

複数レースの平均（`average_tansho_hit` / `average_top3_hit_count` / `average_spearman`）でシステム全体を評価する。

#### 主指標を 3着内的中頭数（top3）にした理由

レース数を拡充すると単勝的中率は小母数バイアスが外れて下がり、実用上は「複勝圏に入る馬を拾えるか」の方が
有用と判断したため、最適化の主目的を top3 に置いている（複勝寄りの予想になる）。

#### 複合スコアとタイブレーク

top3 はレース毎 0〜3 の離散値で同点が出やすい。最適化では同点を割るため、スピアマン相関を十分小さい
重みで足した**複合スコア**を単一目的として最大化する。

```
複合スコア = 平均top3 + 0.001 × 平均スピアマン
```

- top3 平均の「1頭ぶんの差」は `1/レース数` あるのに対し、スピアマンの寄与は最大でも ±0.001。
  通常のレース数では `1/レース数 ≫ 0.001` なので、**top3 が少しでも勝るパラメータは複合スコアでも必ず勝つ**。
- top3 が完全に同点のときだけ、スピアマンが「並びがより正しい方」を選ぶタイブレークとして働く。

### データリーク防止

バックテストの正当性のため、**各馬の戦績は「対象レース日より前」のものだけをスコア算出に使う**。
fixtures にはキャリア全戦績を素のまま保存し、リークフィルタは評価時（`run_backtest.py`）に
レースごとの開催日（`ground_truth` の `race_date`）と各戦績の日付を文字列比較して適用する
（同日の戦績＝本番レース自身も除外）。各結果に `leak_filter_applied: true` を記録する。

### バックテストの実行手順

```bash
cd special-week/backtest

# 1. （任意）対象レースの重賞 race_id を発見し race_manifest.json を生成・追記する。
#    既存 race_manifest.json があれば不要。当て推量せず netkeiba の開催日別一覧から実在 race_id を取得する。
python3 build_race_manifest.py

# 2. 正解ラベル（実着順）を収集する。取得済みレースはスキップ。
python3 collect_ground_truth.py

# 3. オフライン戦績キャッシュ（fixtures）を収集する。取得済みはスキップ。空セル等は馬/行単位でスキップして堅牢に成立させる。
python3 collect_fixtures.py

# 4. バックテストを実行する。引数なしで全対象レースを回す。
python3 run_backtest.py
```

- 対象レースは `race-info/*.yml` と `race_manifest.json` を `load_backtest_targets()` がマージして決める
  （race_id 単位で重複排除、既存 yml を優先）。
- `run_backtest.py` の引数:
  - 引数なし: デフォルト `ScoringParams`（最適化済みの値を反映済み）で全レースを評価する。
  - `--params <yaml>`: 指定した YAML の `ScoringParams` で評価する。最適化前のベースラインを再現したい場合は
    `--params baseline_params.yaml` を渡す。
  - `--run-id <id>`: 出力ファイル名の識別子。ベースライン保護用の予約 ID は `results/baseline_<id>.json`、
    それ以外は実験扱いで `results/phase3_<id>.json` に保存する（**保護対象のベースライン結果を上書きしない**）。

```bash
# 例: ベースライン（最適化前）を再現評価する。
python3 run_backtest.py --params baseline_params.yaml --run-id baseline_check
```

> 1〜3 はネットワークを伴う収集処理。一度収集すれば、4 のバックテスト以降は完全オフライン・決定論的に
> 何度でも再実行できる。

---

## パラメータ最適化（Optuna）

`optimize.py` が `ScoringParams` を Optuna で自動探索し、複合スコア（top3 主＋0.001×スピアマン）を最大化する。

```bash
cd special-week/backtest
python3 optimize.py --n-trials 200            # → best_params.yaml を生成
```

- サンプラーは `TPESampler(seed=42)` で固定。**同一 seed なら結果は再現可能**（best_params.yaml がバイト一致する）。
- 重賞加点（G1/G2/G3 × 1着/3着内/5着内 の9値）は、差分を正の量でサンプリングして積み上げることで
  **`G1≥G2≥G3`（各ランク帯）かつ `1着≥3着内≥5着内`（各グレード）の順序制約を構造的に保証**する（毎試行 assert で検証）。
- 引数:
  - `--n-trials <N>`: 試行回数（デフォルト 200）。
  - `--seed <N>`: サンプラーの固定 seed（デフォルト 42）。
  - `--out <yaml>`: 最良パラメータの保存先（デフォルト `best_params.yaml`）。

### 汎化検証（過学習チェック）

```bash
python3 holdout_validate.py --n-trials 200 --kfold 3
```

訓練データのみで最適化 → 検証データで評価する。`--val-ratio` / `--split-seed` / `--opt-seed` / `--kfold` で
分割や試行を制御できる（すべて固定 seed で決定論的）。

---

## 最適化結果の本番適用

予想本体に `--params` を渡すと、最適化済みパラメータで予想できる。

```bash
cd special-week/special-week
python special-week.py --race_info derby2026.yml --params backtest/best_params.yaml
```

- `--params` を**省略するとデフォルト `ScoringParams`** が使われる。デフォルトは最適化結果を反映済みなので、
  省略時も最適化済みパラメータで予想する。最適化前のベースラインで予想したい場合は
  `--params backtest/baseline_params.yaml` を渡す。
- `best_params.yaml` が「現行 best」。再最適化のたびに上書きされる。過去の最適化結果は `best_params_v*.yaml`
  に退避してあり、最適化前のベースライン値は `baseline_params.yaml` に退避してある。

---

## 現状の到達点

- Optuna 最適化により、最適化前のベースラインに対して主指標 top3 を改善している。
  **具体的な数値はレース数（バックテスト母数）に依存して変動する**ため、ここには固定値を記載しない。
  現在の値は以下で確認する:

```bash
cd special-week/backtest
python3 run_backtest.py --params baseline_params.yaml --run-id baseline_check  # 最適化前
python3 run_backtest.py --run-id optimized_check                               # 最適化後（デフォルト=最適化値）
```

- 汎化は限定的で、訓練では明確に改善する一方、検証側（ホールドアウト/k-fold）の改善は分割により振れる。
  さらなる改善余地があり、次の打ち手（パラメータ次元削減・目的関数の CV 化・データ拡充）は
  `backtest/FUTURE_WORK.md` に整理している。

> `results/` 配下のベースライン保護ファイル（`baseline_*.json`）は、過去の各時点の基準値として温存している。
> 新規実行時は `--run-id` を別 ID にして上書きを避けること。
