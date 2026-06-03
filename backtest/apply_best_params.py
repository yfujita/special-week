# coding: utf-8
"""best_params.yaml の値を scoring_params.py のデフォルト値へ反映するスクリプト。

Optuna 最適化結果（backtest/best_params.yaml）を、special-week/scoring_params.py の
`ScoringParams` データクラスの各フィールドのデフォルト値に書き戻す。
これにより `ScoringParams()`（引数なし）が最新の最適化済み挙動になる。

書き換えるのはデフォルト値だけで、型注釈・行末コメント・インデントは保持する。
未知のキーや型の不整合は事前に検出して中断する（誤反映の防止）。

使い方:
    python apply_best_params.py                       # best_params.yaml を反映
    python apply_best_params.py --params best_params_v3.yaml
    python apply_best_params.py --dry-run             # 差分表示のみ（書き込まない）
"""
import argparse
import os
import re
import sys
from dataclasses import fields

# scraping_common を先に import すると special-week/ 配下（scoring_params 等）へ
# sys.path が通る。optimize.py と同じ流儀に合わせる。
from scraping_common import load_backtest_targets  # noqa: F401,E402
from scoring_params import ScoringParams, load_params_from_yaml  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PARAMS_PATH = os.path.join(HERE, 'best_params.yaml')
SCORING_PARAMS_PATH = os.path.normpath(
    os.path.join(HERE, '..', 'special-week', 'scoring_params.py')
)

# データクラスのフィールド定義行（  name: type = value  # comment）にマッチする。
FIELD_LINE_RE = re.compile(
    r'^(?P<indent>\s+)'
    r'(?P<name>[A-Za-z_]\w*)'
    r'\s*:\s*(?P<type>[A-Za-z_]\w*)'
    r'\s*=\s*(?P<value>.+?)'
    r'(?P<comment>\s*#.*)?$'
)


def format_value(value) -> str:
  """フィールド値を Python ソース上のリテラル表記へ整形する。"""
  if isinstance(value, bool):
    return 'True' if value else 'False'
  if isinstance(value, int):
    return str(value)
  if isinstance(value, float):
    # repr は float を往復可能な最短表記で出力する（精度を落とさない）。
    return repr(value)
  raise TypeError(f'未対応の型です: {type(value).__name__} ({value!r})')


def apply_params(scoring_src: str, params: ScoringParams):
  """scoring_params.py のソース文字列に params を反映し、(新ソース, 変更一覧) を返す。"""
  values = {f.name: getattr(params, f.name) for f in fields(ScoringParams)}
  remaining = set(values)
  changes = []
  out_lines = []

  for line in scoring_src.splitlines(keepends=True):
    match = FIELD_LINE_RE.match(line.rstrip('\n'))
    if match and match.group('name') in remaining:
      name = match.group('name')
      new_literal = format_value(values[name])
      old_literal = match.group('value')
      newline = '\n' if line.endswith('\n') else ''
      comment = match.group('comment') or ''
      rebuilt = (
          f"{match.group('indent')}{name}: {match.group('type')} = "
          f"{new_literal}{comment}{newline}"
      )
      remaining.discard(name)
      if old_literal != new_literal:
        changes.append((name, old_literal, new_literal))
      out_lines.append(rebuilt)
    else:
      out_lines.append(line)

  if remaining:
    raise RuntimeError(
        'scoring_params.py 内に見つからなかったフィールドがあります: '
        f'{sorted(remaining)}'
    )
  return ''.join(out_lines), changes


def main(argv=None) -> int:
  parser = argparse.ArgumentParser(
      description='best_params.yaml を scoring_params.py のデフォルト値へ反映する。'
  )
  parser.add_argument(
      '--params', default=DEFAULT_PARAMS_PATH,
      help=f'反映元の YAML（デフォルト: {DEFAULT_PARAMS_PATH}）'
  )
  parser.add_argument(
      '--target', default=SCORING_PARAMS_PATH,
      help=f'書き換える scoring_params.py（デフォルト: {SCORING_PARAMS_PATH}）'
  )
  parser.add_argument(
      '--dry-run', action='store_true',
      help='書き込まずに差分のみ表示する'
  )
  args = parser.parse_args(argv)

  # load_params_from_yaml が未知キー・欠損を検証してくれる。
  params = load_params_from_yaml(args.params)

  with open(args.target, encoding='utf-8') as f:
    scoring_src = f.read()

  new_src, changes = apply_params(scoring_src, params)

  if not changes:
    print(f'変更なし: {args.target} は既に {args.params} と一致しています。')
    return 0

  print(f'反映元 : {args.params}')
  print(f'対象   : {args.target}')
  print(f'変更数 : {len(changes)} 件')
  for name, old, new in changes:
    print(f'  {name}: {old} -> {new}')

  if args.dry_run:
    print('\n--dry-run のため書き込みはしていません。')
    return 0

  with open(args.target, 'w', encoding='utf-8') as f:
    f.write(new_src)
  print(f'\n書き込み完了: {args.target}')
  return 0


if __name__ == '__main__':
  sys.exit(main())
