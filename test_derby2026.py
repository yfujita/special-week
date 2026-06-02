"""
2026年日本ダービー 動作確認スクリプト（ES投入なし）

special-week の スクレイピング → スコア算出 パイプラインを
ローカルPythonで動作確認する。ES接続は不要。
"""
import sys
import os
import re
import time
import yaml
from typing import Final
from bs4 import BeautifulSoup
import requests

# special-week サブディレクトリのモジュール(tipster, horse)を利用する
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'special-week'))
from tipster import Tipster
from horse import Horse, RaceResult

SCRAPING_HEADERS: Final[dict] = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/120.0.0.0 Safari/537.36'
    )
}

# 2025年以降の馬ページは JS 必須のため、戦績は AJAX エンドポイントから直接取得する
AJAX_HORSE_RESULTS_URL: Final[str] = 'https://db.netkeiba.com/horse/ajax_horse_results.html'
AJAX_HEADERS: Final[dict] = {
    **SCRAPING_HEADERS,
    'Accept': 'application/json, text/javascript, */*; q=0.01',
    'X-Requested-With': 'XMLHttpRequest',
}

# 2025年以降の戦績テーブルは33列構成（インデックス25まで使用）
EXPECTED_MIN_TD_COLS: Final[int] = 26


def build_race_url(race_id: str) -> str:
    return f'https://race.netkeiba.com/race/shutuba.html?race_id={race_id}'


def _extract_horse_id(horse_url: str) -> str:
    """db.netkeiba.com の馬URLから horse_id を取り出す"""
    return horse_url.rstrip('/').split('/')[-1]


def scraping_horse_performance(soup_tr) -> RaceResult:
    td_array = list(soup_tr.find_all('td'))
    if len(td_array) < EXPECTED_MIN_TD_COLS:
        return None
    if not td_array[11].text.strip().isdigit():
        return None
    # 2025年以降の戦績テーブル構造に合わせたインデックス
    difference_raw = td_array[19].text.strip()
    return RaceResult(
        date=td_array[0].text.strip(),
        course=td_array[1].text.strip(),
        weather=td_array[2].text.strip(),
        race_name=td_array[4].text.strip(),
        race_grade=td_array[4].get('class')[0] if (td_array[4].get('class') or []) else '',
        number_of_horses=int(td_array[6].text.strip()),
        popularity=td_array[10].text.strip(),
        ranking=int(td_array[11].text.strip()),
        distance=int(td_array[14].text.strip()[1:]),
        course_type=td_array[14].text.strip()[:1],
        course_condition=td_array[16].text.strip(),
        time=td_array[18].text.strip(),
        difference=float(difference_raw if difference_raw else '0'),
        passing=td_array[25].text.strip(),
    )


def scraping_horse(target_url: str) -> list:
    horse_id = _extract_horse_id(target_url)
    ajax_headers = {
        **AJAX_HEADERS,
        'Referer': f'https://db.netkeiba.com/horse/{horse_id}/',
    }
    response = requests.get(
        AJAX_HORSE_RESULTS_URL,
        params={'input': 'UTF-8', 'output': 'json', 'id': horse_id},
        headers=ajax_headers,
    )
    try:
        payload = response.json()
    except Exception:
        return []

    if payload.get('status') != 'OK' or not payload.get('data'):
        return []

    soup_result_table = BeautifulSoup(payload['data'], 'html.parser').find(class_='db_h_race_results')
    if soup_result_table is None:
        return []
    soup_tbody = soup_result_table.find('tbody')
    if soup_tbody is None:
        return []

    races = []
    for soup_tr in soup_tbody.find_all('tr'):
        race_result = scraping_horse_performance(soup_tr)
        if race_result is not None:
            races.append(race_result)
    return races


def scraping_horses(race_url: str) -> list:
    horses = []
    html = requests.get(race_url, headers=SCRAPING_HEADERS)
    soup = BeautifulSoup(html.content, 'html.parser')
    horse_rows = soup.find_all('tr', class_='HorseList')
    print(f"出走表から {len(horse_rows)} 行を検出")

    for tr_horse in horse_rows:
        td_array = list(tr_horse.find_all('td'))
        # Umaban TD が存在しない行（小計行など）はスキップ
        umaban_td = tr_horse.find('td', class_=re.compile('Umaban.'))
        if umaban_td is None:
            continue
        td_horseinfo = tr_horse.find('td', class_='HorseInfo')
        if td_horseinfo is None:
            continue

        pos = umaban_td.text.strip()
        horse_name = td_horseinfo.text.strip()
        print(f'  Loading... {pos}:{horse_name}', flush=True)

        horse_url = td_horseinfo.find('a').get('href')
        results = scraping_horse(horse_url)
        sex = tr_horse.find('td', class_='Barei').text.strip()[:1]
        age = tr_horse.find('td', class_='Barei').text.strip()[1:]
        additional_weight = td_array[5].text.strip()
        horse = Horse(
            pos=pos,
            horse_name=horse_name,
            sex=sex,
            age=age,
            additional_weight=additional_weight,
            race_results=results,
        )
        horses.append(horse)
        time.sleep(1)

    return horses


def main():
    race_info_path = os.path.join(os.path.dirname(__file__), 'race-info', 'derby2026.yml')
    with open(race_info_path) as f:
        race_info = yaml.load(f, Loader=yaml.FullLoader)

    print(f"=== {race_info['race_name']} ({race_info['race_grade']}) ===")
    print(f"東京 芝{race_info['distance']}m  race_id: {race_info['race_id']}")
    print()

    race_url = build_race_url(str(race_info['race_id']))
    print(f"出走表URL: {race_url}")
    print()

    horses = scraping_horses(race_url)
    print()

    # スコア算出
    tipster = Tipster()
    tipster.execute(race_info, horses)

    print()
    print("=== スコアランキング ===")
    horses_sorted = sorted(horses, key=lambda h: h.score, reverse=True)
    run_type_names = {1: '逃げ', 2: '先行', 3: '差し', 4: '追込'}
    for rank, horse in enumerate(horses_sorted, start=1):
        results_count = len(horse.race_results)
        run_type_label = run_type_names.get(horse.getRunType(), '?')
        print(
            f"  {rank:2d}位  {horse.pos:>2s}番 {horse.horse_name:<16s}"
            f"  score={horse.score:8.2f}  戦績{results_count:2d}戦"
            f"  脚質={run_type_label}"
        )

    print()
    print("=== 全馬データ（馬番順） ===")
    for h in sorted(horses, key=lambda h: int(h.pos)):
        print(
            f"  {h.pos:>2s}番 {h.horse_name:<16s}"
            f"  score={h.score:8.2f}"
            f"  {h.sex}{h.age}歳  斤量{h.additional_weight}kg"
        )


if __name__ == '__main__':
    main()
