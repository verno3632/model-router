# model-router

コーディングエージェント向けの skill。タスクの役割（計画・裏取り・調査・実装・UI・レビュー）ごとに、Devin SWE-2 / Claude / Codex / Grok のどれへ委譲するかを決める。利用上限に当たった製品を記録し、フォールバック先を切り替える仕組みも同梱する。

Claude Code と Codex の両方から同じ skill を読む前提で書いてある。

## 中身

| ファイル | 役割 |
|---|---|
| `SKILL.md` | エージェントが読む本体。原則、委譲の入口、役割表、SWE-2 へ出す仕事の境界、枠が死んだときの手順 |
| `limits.py` | 枠切れの記録。標準ライブラリのみ、Python 3 |

判定はエージェント本人が `SKILL.md` の役割表を読んでする。外部の分類器やルーターは使わない。

## 置き方

```sh
git clone https://github.com/verno3632/model-router ~/.agents/skills/model-router
ln -s ~/.agents/skills/model-router ~/.claude/skills/model-router   # Claude Code から読む場合
```

`SKILL.md` は `limits.py` を `~/.agents/skills/model-router/limits.py` の絶対パスで呼ぶ。別の場所へ置くならそこを書き換える。

エージェントに確実に読ませるには、`CLAUDE.md` / `AGENTS.md` に一行入れる。

```md
委譲・モデル選定の前に `model-router` skill を読む。
```

## 考え方

- **枠の優先順は Devin → Claude → Codex → Grok。** 無料の SWE-2 を使い倒し、Codex の Astra は UI のために残し、上限の近い Grok は最後。役割表のフォールバック列はこの順に並ぶ。
- **高いモデルは考える役へ。** 設計、画面を見る仕事、別ファミリーのレビュー。書く・直す・量産は SWE-2。
- **書いた者と見る者を分ける。** 計画したモデルに実装もレビューもさせない。
- **委譲の入口は Orca の独立 CLI セッション一本。** 内蔵サブエージェントと CLI の直接ヘッドレス起動は入口にしない。

表の中身（モデル名、努力レベル、無料期限）は作者の契約と手元の環境に合わせたもの。使うなら自分の環境に書き換える。

## limits.py

親セッションは子セッションを `run` 経由で起動する。`run` は起動前に記録を見て、死んでいる段なら起動しない。起動した子が上限の文言を出して終わったら、記録してから終了コード 75 を返す。親は 75 を見たら次の段へ進む。

```sh
L=~/.agents/skills/model-router/limits.py
python3 $L status                                   # 製品・モデルごとの生死
python3 $L first swe sonnet codex:luna grok         # 並びの中で生きている最初の段
python3 $L run swe -- devin --model swe-2-medium -p "..."   # 非対話の子を起動
orca terminal read --terminal <handle> | python3 $L scan codex:astra   # TUI の子の出力を読ませる
python3 $L mark codex:astra --for 5h                # 手で記録（30m / 5h / 3d、上限 7d）
python3 $L clear codex:astra                        # 記録を消す
```

キーは、製品ごと止まったら `swe` / `claude` / `codex` / `grok`。モデルだけ止まったら `haiku` / `sonnet` / `opus` / `fable` / `codex:<モデル名>`。製品のキーが冷却中なら、その製品のモデルもすべて死んでいる扱いになる。

記録は `~/.agents/model-router-limits.json`（`{キー: 期限の epoch ミリ秒}`）。全セッションで共有し、期限が来れば無視される。環境変数 `MODEL_ROUTER_LIMITS_FILE` で差し替えられる。

### 週枠の余り具合（budget）

```sh
python3 $L budget            # 5 分キャッシュ。--refresh で取り直す
# claude  余り   週 51% 使用 / 87% 経過（+36）。リセット 04:00。5h 枠 24%
# codex   普通   週 1% 使用 / 0% 経過（-1）。リセット 09-27 05:38
```

使用率ではなくペース（週の経過割合 − 使用率）で見る。+25 ポイント以上で余り、−15 ポイント以下か使用率 85% 以上で節約。Claude は 5 時間枠が 80% を超えていると余りにしない。余りのときに何を格上げするかは `SKILL.md` の「週枠の余り具合」にある。実装は余っていても SWE-2 のまま。

- Claude は macOS の Keychain（`Claude Code-credentials`）、Codex は `~/.codex/auth.json` のトークンで各社の利用状況 API を読む。どちらも非公開の API で、形が変われば取得できず「普通」に倒れる。
- トークンはどこにも書き出さない。キャッシュ `~/.agents/model-router-budget.json` に残るのは割合と時刻だけ。
- API が「上限に達した」「このモデルは利用不可」と返した枠は、枠切れの記録へ自動で写す。
- Devin と Grok は利用率を取れないので対象外。

### 限界

- 上限の検出は `usage limit` / `rate limit` / `limit reached` / `429` / `too many requests` などの一般的な文言の一致で、各 CLI が実際に出す文言で確かめたものではない。拾えなかったら `mark` で手で書き、文言を `LIMIT_RE` に足す。
- 見るのは出力の末尾 30 行だけ。それでも、報告の最後に「rate limit」と書かれていれば誤って記録する。そのときは `clear`。
- TUI の子は `run` を通せない。起動前に `first` で段を決め、止まって見えたら `scan` に読ませる。親が `scan` を忘れると記録は残らない。
- 復活時刻は `try again in 3 hours 12 minutes` と `resets at 3pm` の形だけ読める。読めなければ 5 時間で記録する。
- SWE-2 の無料期限（2026-10-10）が `limits.py` に埋めてあり、過ぎると `swe` を × にする。
