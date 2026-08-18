import QtQuick
import Quickshell.Io

Item {
  id: root

  property var shell: null
  property var manifest: null

  property bool ready: false
  property var config: ({ default: { browser: "" }, rules: [] })
  property var browsers: []
  property string revision: ""
  property string configPath: ""
  property string sourcePath: ""
  property bool legacyConfig: false
  property string currentDefault: ""
  property string currentDefaultName: "Not set"
  property bool isDefault: false
  property bool handlerNeedsRepair: false
  property bool desktopInstalled: false
  property bool canSetDirect: false
  property string initializationError: ""

  property var pendingJobs: []
  property var activeJob: null
  property int requestSerial: 0
  property string operationOutput: ""
  property string operationError: ""
  property bool helperStarted: false
  property bool helperTimedOut: false

  readonly property string pluginDir: manifest && manifest.__sourceDir
    ? String(manifest.__sourceDir)
    : ""
  readonly property string helperPath: pluginDir !== ""
    ? pluginDir.replace(/\/$/, "") + "/scripts/choosium.py"
    : ""
  readonly property int ruleCount: config && Array.isArray(config.rules)
    ? config.rules.length
    : 0

  signal response(string requestId, string operation, var result)

  function hasOperation(operation) {
    if (activeJob && activeJob.operation === operation) return true
    for (var i = 0; i < pendingJobs.length; i++) {
      if (pendingJobs[i].operation === operation) return true
    }
    return false
  }

  function request(operation, payload, requestId, priority) {
    requestSerial++
    var id = requestId || (operation + ":" + requestSerial)
    var queue = pendingJobs.slice()
    queue.push({
      operation: String(operation),
      payload: payload || {},
      requestId: String(id),
      priority: priority === undefined ? 50 : Number(priority),
      serial: requestSerial
    })
    queue.sort(function(left, right) {
      if (left.priority !== right.priority) return right.priority - left.priority
      return left.serial - right.serial
    })
    pendingJobs = queue
    Qt.callLater(startNext)
    return id
  }

  function refresh(requestId) {
    if (hasOperation("status")) return ""
    return request("status", {}, requestId || "service:status", 100)
  }

  function initialize() {
    if (helperPath === "" || hasOperation("status") || ready) return
    refresh("service:status")
  }

  function startNext() {
    if (activeJob || helperProc.running || pendingJobs.length === 0) return
    if (helperPath === "") {
      initializeTimer.restart()
      return
    }

    var queue = pendingJobs.slice()
    activeJob = queue.shift()
    pendingJobs = queue
    operationOutput = ""
    operationError = ""
    helperStarted = false
    helperTimedOut = false
    helperProc.command = ["python3", helperPath, activeJob.operation]
    helperTimeout.restart()
    helperProc.running = true
  }

  function failedResult(exitCode) {
    var detail = String(operationError || "").trim()
    if (detail.length > 300) detail = detail.slice(0, 300) + "..."
    return {
      ok: false,
      code: exitCode === 127 ? "missing-dependency" : "helper-failed",
      error: detail || "Choosium's helper exited without a response."
    }
  }

  function applyState(result) {
    var state = result && result.state && result.state.ok ? result.state : result
    if (!state || !state.ok || !state.config) return
    config = state.config
    browsers = state.browsers || []
    revision = String(state.revision || "")
    configPath = String(state.configPath || "")
    sourcePath = String(state.sourcePath || configPath)
    legacyConfig = !!state.legacyConfig
    currentDefault = String(state.currentDefault || "")
    currentDefaultName = String(state.currentDefaultName || "Not set")
    isDefault = !!state.isDefault
    handlerNeedsRepair = !!state.handlerNeedsRepair
    desktopInstalled = !!state.desktopInstalled
    canSetDirect = !!state.canSetDirect
    ready = true
    initializationError = ""
  }

  function finishActive(exitCode) {
    var job = activeJob
    if (!job) return
    helperTimeout.stop()

    var result = null
    var raw = String(operationOutput || "").trim()
    if (helperTimedOut) {
      result = { ok: false, code: "helper-timeout", error: "Choosium's helper timed out." }
    } else if (raw !== "") {
      try {
        result = JSON.parse(raw)
      } catch (error) {
        result = { ok: false, code: "helper-invalid", error: "Choosium's helper returned invalid data." }
      }
    } else {
      result = failedResult(exitCode)
    }

    applyState(result)
    if (!result.ok && job.operation === "status") {
      ready = false
      initializationError = String(result.error || "Could not load Choosium.")
    }

    activeJob = null
    helperStarted = false
    helperTimedOut = false
    response(job.requestId, job.operation, result)
    Qt.callLater(startNext)
  }

  onHelperPathChanged: initialize()
  Component.onCompleted: initializeTimer.start()

  Timer {
    id: initializeTimer
    interval: 100
    onTriggered: root.initialize()
  }

  Timer {
    id: helperTimeout
    interval: 15000
    onTriggered: {
      if (!root.activeJob) return
      root.helperTimedOut = true
      root.operationError = "Choosium's helper timed out."
      if (helperProc.running) helperProc.signal(9)
      else root.finishActive(1)
    }
  }

  Process {
    id: helperProc
    stdinEnabled: true

    onStarted: {
      root.helperStarted = true
      write(JSON.stringify(root.activeJob ? root.activeJob.payload : {}) + "\n")
    }

    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.operationOutput = text
    }

    stderr: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.operationError = text
    }

    onRunningChanged: {
      if (!running && root.activeJob && !root.helperStarted) {
        Qt.callLater(function() {
          if (!helperProc.running && root.activeJob && !root.helperStarted)
            root.finishActive(127)
        })
      }
    }

    onExited: function(exitCode, exitStatus) {
      helperTimeout.stop()
      Qt.callLater(function() { root.finishActive(exitCode) })
    }
  }
}
