import type { TranslationKey } from "./en";

const ja: Record<TranslationKey, string> = {
  // App
  "app.title": "ミスターDのAIオフィス",
  "app.initializingSystems": "システム起動中...",

  // Header Controls
  "header.simulate": "シミュレート",
  "header.reset": "リセット",
  "header.clearDb": "DB消去",
  "header.debugOn": "デバッグ ON",
  "header.debugOff": "デバッグ OFF",
  "header.settings": "設定",
  "header.help": "ヘルプ",
  "header.status": "状態",
  "header.connected": "接続中",
  "header.disconnected": "切断",
  "header.aiOn": "ON",
  "header.aiOff": "OFF",
  "header.agents": "エージェント",

  // Modals
  "modal.confirmDbWipe": "データベース消去の確認",
  "modal.cancel": "キャンセル",
  "modal.wipeAllData": "全データを消去",
  "modal.wipeWarning":
    "セッション履歴とイベントをすべて永久に削除しますか？この操作は取り消せず、可視化の現状もリセットされます。",
  "modal.keyboardShortcuts": "キーボードショートカット",
  "modal.close": "閉じる",
  "modal.toggleDebug": "デバッグ表示切替",
  "modal.showAgentPaths": "エージェントの移動経路を表示",
  "modal.showQueueSlots": "キュー位置を表示",
  "modal.showPhaseLabels": "フェーズラベルを表示",
  "modal.deleteSession": "セッションを削除",
  "modal.delete": "削除",
  "modal.deleteSessionConfirm": "次のセッションを削除してよろしいですか？",
  "modal.deleteSessionWarning": "削除されます:",
  "modal.events": "イベント",
  "modal.cannotBeUndone": "この操作は取り消せません。",

  // Settings
  "settings.title": "設定",
  "settings.clockType": "時計の種類",
  "settings.analog": "アナログ",
  "settings.digital": "デジタル",
  "settings.timeFormat": "時刻形式",
  "settings.12hour": "12時間",
  "settings.24hour": "24時間",
  "settings.sessionBehavior": "セッションの挙動",
  "settings.autoFollow": "新しいセッションに自動追従",
  "settings.autoFollowDesc":
    "同じプロジェクト内で新しいセッションが開いたら自動で切り替える",
  "settings.clockTip":
    "ヒント: オフィス内の時計をクリックすると表示モードを切り替えられます。",
  "settings.language": "言語",

  // Sessions
  "sessions.title": "セッション一覧",
  "sessions.loading": "セッションを読み込み中...",
  "sessions.noSessions": "セッションはまだありません",
  "sessions.unknownProject": "プロジェクト不明",
  "sessions.deleteSession": "セッションを削除",
  "sessions.events": "イベント",
  "sessions.events_one": "{count} 件",
  "sessions.events_other": "{count} 件",
  "sessions.expandSidebar": "サイドバーを展開",
  "sessions.collapseSidebar": "サイドバーを折りたたむ",
  "sessions.dragToResize": "ドラッグしてサイズ変更",

  // Right Sidebar
  "sidebar.events": "イベント",
  "sidebar.conversation": "会話",

  // Event Log
  "eventLog.title": "イベントログ",
  "eventLog.events": "件",
  "eventLog.events_one": "{count} 件",
  "eventLog.events_other": "{count} 件",
  "eventLog.waiting": "イベント待機中...",

  // Agent Status
  "agentStatus.title": "エージェント状態",
  "agentStatus.agents": "人",
  "agentStatus.agents_one": "{count} 人",
  "agentStatus.agents_other": "{count} 人",
  "agentStatus.noAgents": "稼働中のエージェントなし",
  "agentStatus.agent": "エージェント",
  "agentStatus.desk": "机",
  "agentStatus.noTaskSummary": "タスク要約なし",
  "agentStatus.noRecentToolCall": "直近のツール呼び出しなし",
  "agentStatus.inQueue": "{queueType} 待ち (順位 {position})",

  // Git Status
  "git.title": "Git 状態",
  "git.waitingForStatus": "git 状態を待機中...",
  "git.noSession": "セッション未選択",
  "git.noRepo": "Git リポジトリが見つかりません",
  "git.changedFiles": "変更ファイル",
  "git.staged": "ステージ済み",
  "git.recentCommits": "最近のコミット",
  "git.noCommits": "コミットが見つかりません",
  "git.modified": "変更",
  "git.added": "追加",
  "git.deleted": "削除",
  "git.renamed": "リネーム",
  "git.copied": "コピー",
  "git.untracked": "未追跡",
  "git.ignored": "無視",

  // Conversation
  "conversation.title": "会話",
  "conversation.msgs": "件",
  "conversation.msgs_one": "{count} 件",
  "conversation.msgs_other": "{count} 件",
  "conversation.thinking": "思考中",
  "conversation.showMore": "もっと見る",
  "conversation.collapse": "折りたたむ",
  "conversation.claude": "Claude",
  "conversation.showFullResponse": "全文を表示",
  "conversation.hideToolCalls": "ツール呼び出しを隠す",
  "conversation.showToolCalls": "ツール呼び出しを表示",
  "conversation.expandConversation": "会話を拡大",
  "conversation.close": "閉じる",
  "conversation.noConversation":
    "会話はまだありません。Claude Code セッションを開始してください。",

  // Event Detail Modal
  "eventDetail.summary": "要約",
  "eventDetail.tool": "ツール",
  "eventDetail.agentName": "エージェント名",
  "eventDetail.taskDescription": "タスク内容",
  "eventDetail.userPrompt": "ユーザープロンプト",
  "eventDetail.thinking": "思考",
  "eventDetail.message": "メッセージ",
  "eventDetail.resultSummary": "結果の要約",
  "eventDetail.errorType": "エラー種別",
  "eventDetail.toolInput": "ツール入力",
  "eventDetail.noDetail": "このイベントに詳細情報はありません。",

  // Loading Screen
  "loading.office": "オフィスを読み込み中...",

  // Zoom Controls
  "zoom.in": "拡大",
  "zoom.out": "縮小",
  "zoom.reset": "倍率リセット",

  // Mobile
  "mobile.menu": "メニュー",
  "mobile.agentActivity": "エージェントの動き",
  "mobile.boss": "司令塔",
  "mobile.noActiveAgents": "稼働中のエージェントなし",

  // Status Messages
  "status.switchedToSession": "セッション {sessionId} に切替...",
  "status.deletingSession": "セッション {sessionId} を削除中...",
  "status.sessionDeleted": "セッションを削除しました。",
  "status.failedDeleteSession": "セッションの削除に失敗しました。",
  "status.errorConnecting": "バックエンドへの接続に失敗しました。",
  "status.clearingDatabase": "データベースを消去中...",
  "status.databaseCleared": "データベースを消去しました。",
  "status.failedClearDatabase": "データベースの消去に失敗しました。",
  "status.triggeringSimulation": "シミュレーションを起動中...",
  "status.simulationStarted": "シミュレーションを開始しました！",
  "status.failedSimulation": "シミュレーションの起動に失敗しました。",
  "status.storeReset": "ストアをリセットしました。",
  "status.sessionDeletedSwitched":
    "セッションを削除しました。{sessionName} に切替。",
  "status.sessionDeletedNoOthers":
    "セッションを削除しました。ほかに利用可能なセッションはありません。",
  "status.connectedTo": "{sessionName} に接続しました",
  "status.autoFollowed": "新しいセッションを自動追従: {sessionName}",
};

export default ja;
