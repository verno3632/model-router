---
name: model-router
description: モデル選定と委譲先の決定。計画・裏取り・調査・実装・UI・レビューを Devin SWE-2 / Claude / Codex / Grok の誰に任せるか決めるとき、委譲する前、利用上限に当たったときに読む。
---

# モデル選定

優先順は ユーザー指定 → プロジェクト AGENTS.md → この skill。プロジェクトの指定はそのプロジェクトの中だけで効かせる（あるプロジェクトが管理役に Astra を指定していても、他のプロジェクトへは広げない）。

## 原則

- **枠の優先順は Devin → Claude → Codex → Grok。** SWE-2 は無料なので使い倒す。Codex は Astra を UI のために残し、UI 以外では Sol / Luna を先に使う。Grok はプランが低く上限が近いので最後。例外は X の調査で、Grok にしか見られないので本命側で使う。表のフォールバック列はこの順に並べてある。
- **高いモデルは考える役へ。** 設計、画面を見る仕事、別ファミリーのレビュー。書く・直す・量産は Devin × SWE-2 が本命。
- **新しい実装はまず「SWE-2 で足りるか」を見る。** 足りるなら他を開かない。lint・typo・余白・単純テスト追加は SWE-2 の仕事で、Fable / Astra の枠を使わない。
- **昇格は同じ失敗 2 回で。** SWE-2 が 2 回つまずいたら Fable か Sol High へ上げる。同じチケットを Max で回し続けない。
- **書いた者と見る者を分ける。** 計画したモデルに実装もレビューもさせない。SWE-2 に自己レビューさせない。Astra が書いた UI は Opus 5、Opus が書いたものは Sol High が見る。
- **UI は Astra が見た目を決めてから SWE-2。** Astra には参照画像・既存画面・スクショのどれかを付けて渡す。テキストだけの brief（「いい感じのサイト」）には参照を足してから投げる。見た目が固まったら SWE-2 が CSS / コンポーネント化する。

## 委譲の入口

Orca の独立 CLI セッション一本。先に `orca-cli` と `orchestration` skill を読む。Devin / SWE-2 もここから起動する。Codex 内蔵サブエージェントと CLI の直接ヘッドレス起動は入口にしない。内蔵を使ってよいのは表の「リポジトリ探索」だけ。

- 会話は渡せない。タスクの記述だけで完結する独立した仕事だけ外へ出す。
- 起動引数でモデルと推論を指定し、起動結果で実際のモデルを確認する。設定を保存しただけで「切り替えた」と報告しない。重要なレビューの例：`codex -c 'model="gpt-5.6-sol"' -c 'model_reasoning_effort="high"' review`
- 本命が使えないときは未実行で止めず、表のフォールバックへ進んで退避先を報告する。表にないモデルへ黙って置き換えない。
- Orca 自体が使えないときだけ `agent-relay` / `swe-relay` skill へ退避し、退避したと報告する。

## 役割表

モデルと努力レベルの正本はこの表だけ。上から工程順：計画 → 計画レビュー → 実装 → UI の見た目 → UI 量産 → 実装レビュー。レビュー後もまだ不安なら Codex × Sol High でもう一度見る。

| 役割 | 本命 | 努力 | 同一ツール内の降格 | 別AIのフォールバック |
|---|---|---|---|---|
| 企画・進行管理（分割・監督） | 現在の対話担当。Codex なら Terra High | High | — | — |
| 要件整理・設計・Plan | Claude Code × Fable 5.1 | High / xHigh | Opus 5 High | Codex × Sol High → Astra High |
| 計画のレビュー | Codex × Astra High | High | Sol High | Claude × Opus 5（新セッション） |
| 裏取り（実コード・既存テスト・公式資料の調査） | Devin × SWE-2 Medium | Medium | — | Claude × Sonnet 5 → Codex × Luna max |
| 外部・Web 調査（ライブラリ比較、技術選定の材料集め、公式ドキュメント・事例）。結論の判断は対話担当 | Devin × SWE-2 Medium。X 上の情報は Grok 4.6 を併走させる | Medium | SWE-2 High | Claude × Sonnet 5 → Codex × Luna。Grok が死んでいたら X の調査を省き、省いたと報告 |
| リポジトリ探索（対話中の軽い確認） | Claude × Haiku 4.5 サブエージェント | Low | Sonnet 5 | Devin × SWE-2 Medium → Codex × Luna |
| 普通の実装 | Devin × SWE-2 Medium | Medium | SWE-2 High に昇格 | Claude × Sonnet 5 → Codex × Luna Extra High → Grok 4.6 |
| 難しい実装 | Devin × SWE-2 High / Max | High。横断・マイグレーションは Max | SWE-2 Medium | Claude × Opus 5 → Codex × Sol High → Grok 4.6 |
| 詰まったデバッグ | Devin × SWE-2 High | High。2 回失敗したら Max | — | Claude × Fable 5.1 → Codex × Sol High → Astra High |
| 機械的な量産・定型・並列探索（lint、テスト追加、走査） | Devin × SWE-2 Medium。量があれば並列 | Medium | — | Claude × Haiku 4.5（足りなければ Sonnet 5）→ Codex × Luna → Grok 4.5 / 4.6 |
| 夜間・非同期チケット | Devin × SWE-2 High 単体 | High | Fusion（Fable Medium + SWE-2）は計画が曖昧なときだけ | Claude × Sonnet 5 → Codex × Luna Extra High → Grok 4.6 |
| UI の見た目（スクショ・ブラウザ QA、参照・モック・既存画面の再現、LP、空間・3D・モーション） | Codex × Astra High | High | Sol High | Claude × Fable 5.1 / Opus 5（スクショ必須）→ Grok 4.6 |
| UI：見た目が固まったあとの CSS 量産 | Devin × SWE-2 Medium | Medium | SWE-2 High | Claude × Sonnet 5 → Codex × Luna |
| 実装レビュー | Claude Code × Opus 5（新セッション） | Medium〜High | Sonnet 5 | Codex × Sol High → Grok 4.6 |

## SWE-2 の境界

無料は 2026-10-10 まで。それまでは使い放題。過ぎていたら SWE-2 全滅として扱い、本人へこの skill の見直しを促す。

**出す**：仕様が固まった実装、リファクタ、型直し、テスト追加、lint、依存上げ、単純バグ、Astra が決めた UI の CSS / コンポーネント化、夜間チケット、並列の機械的作業、API や型の一括置換、fixture・モック、アクセシビリティ、レスポンシブ、ログや設定値の統一、マイグレーション用スクリプト、ドキュメント整備、外部・Web 調査の材料集め。

**出さない**（フォールバック先にも選ばない）：設計・Plan、計画レビュー、実装レビュー、スクショ差分、ブラウザ QA、Figma 操作、参照からの見た目の初稿、3D / モーション / 空間 UI、何を作るかまだ決まっていない仕事、認証・権限・課金・DB移行・セキュリティ境界・データ整合性の判断。

## 枠が死んだとき

子セッションは必ず同梱の `limits.py` を通して起動する。起動前に記録を見て、子が上限で止まったら自動で記録する。

```sh
L=~/.agents/skills/model-router/limits.py
python3 $L status                                  # 全体を見る
python3 $L first swe sonnet codex:luna grok        # フォールバックの並びから、生きている最初の段を返す
python3 $L run swe -- devin --model swe-2-medium -p ...   # 非対話の子。Orca の `terminal create --command` にこの形で渡す
orca terminal read --terminal <handle> | python3 $L scan codex:astra   # TUI の子。止まって見えたら読ませる
python3 $L mark codex:astra --for 5h               # 自動で拾えなかったとき手で記録
python3 $L clear codex:astra                       # 早く復活したとき
```

- `run` は、その段が死んでいれば子を起動せず終了コード 75 を返す。起動した子が上限の文言を出して終わったときも、記録してから 75 を返す。**75 を見たら次の段へ進む。** それ以外の終了コードは子のもの。
- TUI の子は `run` を通せない（端末を直接つかむため）。起動前に `first` で段を決め、止まって見えたら `scan` に端末の出力を読ませる。75 なら記録済み。
- `run` も `scan` も出力の末尾 30 行だけを見る。復活時刻が出力から読めればその時刻まで、読めなければ 5h で記録する。

本命が死んでいたら、別製品へ飛ぶ前に同じツールの一段下（表の「降格」列）。それも死んでいたら「別AIのフォールバック」列を左から進み、死んでいる段は飛ばす。

- キーは、製品ごと止まったら `swe` / `claude` / `codex` / `grok`。モデルだけ止まったら `haiku` / `sonnet` / `opus` / `fable` / `codex:astra` / `codex:sol` / `codex:luna` / `codex:terra`。
- `--for` にはエラーに出た復活時刻までの長さを入れる（`30m` / `5h` / `3d`、上限 7d）。分からなければ既定の 5h。
- 記録は `~/.agents/model-router-limits.json` で全セッションが共有し、期限が来れば自動で消える。書かれるのは `run` / `scan` / `mark` を通ったときだけなので、子の起動をここから外さない。
- SWE-2 の無料期限（2026-10-10）を過ぎると status が `swe` を × にする。

編成ごとの決まり：

- SWE-2 全滅 → 各行のフォールバック列へ。
- Fusion の Fable lead 切れ → SWE-2 単体。
- Astra と Fable が同時に死んだ第二編成は SWE-2 + Opus 5。
