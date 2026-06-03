# numpy>=2.0 / optuna>=4.0 は Python 3.9+ が必要なため 3.12 を使う
# （3.8 では numpy 2.x の対応 wheel が無く pip install が失敗する）
FROM python:3.12
USER root

RUN apt-get update
RUN apt-get -y install locales && \
    localedef -f UTF-8 -i ja_JP ja_JP.UTF-8
ENV LANG ja_JP.UTF-8
ENV LANGUAGE ja_JP:ja
ENV LC_ALL ja_JP.UTF-8
ENV TZ JST-9
ENV TERM xterm

RUN apt-get install -y vim less
RUN pip install --upgrade pip && pip install --upgrade setuptools

COPY requirements.txt /app/
RUN pip install -r /app/requirements.txt
RUN mkdir -p /opt/race-info/

#ファイルをappディレクトリに追加
COPY special-week.py /app/
COPY sp_es.py /app/
COPY horse.py /app/
COPY tipster.py /app/
COPY scoring_params.py /app/
COPY es_files /app/es_files/

#ルートディレクトリ設定
WORKDIR /app
#コマンド実行
CMD ["python", "special-week.py", "--race_info", "race-info.yml"]
