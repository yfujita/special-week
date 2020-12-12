# special-week

競馬予想支援

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