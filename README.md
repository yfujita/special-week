# special-week

競馬予想支援

## Usage

`race-info/race-info.yml` を編集。IDは `https://race.netkeiba.com` のものを記載。

### Elasticsearch起動

```
$ bash run.sh
```

### Queryを実行
```
$ cd query
$ bash query-test.sh general.json
$ cat outputs/out-general.json
```