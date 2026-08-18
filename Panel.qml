pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Controls as QQC
import qs.Commons as C
import qs.Ui as Ui
import "Model.js" as Model

Ui.Panel {
  id: root
  moduleName: "io.github.filipechagas.choosium"
  manageIpc: false

  property var anchorItem: null
  property var host: null
  property var service: null

  property string page: "dashboard"
  property var rules: []
  property string fallbackBrowser: ""
  property bool busy: false
  property string pendingRequestId: ""
  property string pendingAction: ""
  property string refreshRequestId: ""
  property int requestSerial: 0
  property string statusText: ""
  property string statusKind: "info"

  property int editingIndex: -1
  property string editorRevision: ""
  property string editorName: ""
  property string editorBrowser: ""
  property var editorClients: []
  property var editorDomains: []
  property int deleteIndex: -1

  readonly property string instanceKey: Date.now().toString(36)
    + "-" + Math.floor(Math.random() * 0x1000000).toString(36)
  readonly property color foreground: C.Color.popups.text
  readonly property color muted: C.Color.muted
  readonly property color accent: C.Color.accent
  readonly property color urgent: C.Color.urgent
  readonly property string fontFamily: bar ? bar.fontFamily : C.Style.font.family
  readonly property var browsers: service ? service.browsers : []
  readonly property string helperPath: service ? String(service.helperPath || "") : ""
  readonly property bool serviceReady: service ? !!service.ready : false
  readonly property bool routingHealthy: serviceReady
    && service && service.isDefault && service.desktopInstalled
  readonly property bool handlerNeedsRepair: serviceReady
    && service && (service.handlerNeedsRepair || (service.isDefault && !service.desktopInstalled))

  function nextRequestId(kind) {
    requestSerial++
    return "panel:" + instanceKey + ":" + kind + ":" + requestSerial
  }

  function setStatus(message, kind) {
    statusText = String(message || "")
    statusKind = String(kind || "info")
    if (statusText !== "" && opened)
      Qt.callLater(function() { root.reveal(statusSurface) })
  }

  function resetScroll() {
    if (panelScroll) panelScroll.contentY = 0
  }

  function reveal(item) {
    if (!item || !panelScroll || panelScroll.height <= 0) return
    var point = item.mapToItem(contentColumn, 0, 0)
    var top = point.y
    var bottom = top + item.height
    if (top < panelScroll.contentY)
      panelScroll.contentY = Math.max(0, top)
    else if (bottom > panelScroll.contentY + panelScroll.height)
      panelScroll.contentY = Math.min(
        Math.max(0, panelScroll.contentHeight - panelScroll.height),
        bottom - panelScroll.height)
  }

  function syncFromService() {
    if (!service || !service.config) return
    fallbackBrowser = String(service.config.default && service.config.default.browser || "")
    rules = Model.cloneRules(service.config.rules)
  }

  function refresh() {
    if (!service || service.hasOperation("status")) return
    refreshRequestId = nextRequestId("status")
    service.request("status", {}, refreshRequestId, 100)
  }

  function requestOperation(operation, payload, action) {
    if (!service || busy) return ""
    busy = true
    pendingAction = String(action || operation)
    pendingRequestId = nextRequestId(operation)
    service.request(operation, payload || {}, pendingRequestId, 120)
    return pendingRequestId
  }

  function persist(nextFallback, nextRules, action, expectedRevision) {
    var revision = expectedRevision === undefined
      ? (service ? String(service.revision || "") : "")
      : String(expectedRevision)
    return requestOperation("save", {
      config: {
        default: { browser: String(nextFallback || "") },
        rules: Model.cloneRules(nextRules)
      },
      expectedRevision: revision
    }, action)
  }

  function changeFallback(value) {
    var selected = String(value || "")
    if (!selected || selected === fallbackBrowser || busy) return
    fallbackBrowser = selected
    persist(selected, rules, "fallback")
  }

  function beginAdd() {
    editingIndex = -1
    editorRevision = service ? String(service.revision || "") : ""
    editorName = ""
    editorBrowser = fallbackBrowser
    editorClients = []
    editorDomains = []
    setStatus("")
    page = "editor"
    resetScroll()
    Qt.callLater(function() { ruleNameField.forceActiveFocus() })
  }

  function beginEdit(index) {
    if (index < 0 || index >= rules.length) return
    var rule = rules[index]
    editingIndex = index
    editorRevision = service ? String(service.revision || "") : ""
    editorName = String(rule.name || "")
    editorBrowser = String(rule.browser || fallbackBrowser)
    editorClients = Model.uniqueStrings(rule.clients)
    editorDomains = Model.uniqueStrings(rule.url)
    setStatus("")
    page = "editor"
    resetScroll()
    Qt.callLater(function() { ruleNameField.forceActiveFocus() })
  }

  function leaveEditor() {
    page = "dashboard"
    setStatus("")
    resetScroll()
  }

  function addManualClient() {
    var value = clientField.text.trim()
    if (!value) return
    editorClients = Model.addUnique(editorClients, value)
    if (!editorName) editorName = Model.titleFromClient(value)
    clientField.text = ""
  }

  function addDomain() {
    var value = Model.cleanDomain(domainField.text)
    if (!value) {
      setStatus("Enter a website such as github.com.", "error")
      domainField.forceActiveFocus()
      return
    }
    editorDomains = Model.addUnique(editorDomains, value)
    domainField.text = ""
    setStatus("")
  }

  function saveEditor() {
    if (!service || String(service.revision || "") !== editorRevision) {
      setStatus("Routes changed in another panel. Go back and reopen this route before saving.", "error")
      return
    }
    var candidate = {
      name: editorName,
      browser: editorBrowser,
      clients: editorClients,
      url: editorDomains
    }
    var error = Model.ruleError(candidate, rules, editingIndex)
    if (error) {
      setStatus(error, "error")
      return
    }
    var next = Model.upsertRule(rules, editingIndex, candidate)
    persist(fallbackBrowser, next, "rule", editorRevision)
  }

  function moveRule(index, direction) {
    if (busy) return
    persist(fallbackBrowser, Model.moveRule(rules, index, direction), "move")
  }

  function askDelete(index) {
    if (busy || index < 0 || index >= rules.length) return
    deleteIndex = index
    deleteDialog.message = "Delete the route '" + String(rules[index].name || "") + "'?"
    deleteDialog.opened = true
    panelFocus.forceActiveFocus()
  }

  function confirmDelete() {
    deleteDialog.opened = false
    if (deleteIndex < 0) return
    persist(fallbackBrowser, Model.removeRule(rules, deleteIndex), "delete")
    deleteIndex = -1
  }

  function makeDefault() {
    requestOperation("set-default", {}, "set-default")
  }

  function open() {
    controller.show()
    syncFromService()
    refresh()
  }

  function close() {
    deleteDialog.opened = false
    controller.hide()
  }

  function toggle() {
    if (opened) close()
    else open()
  }

  function handleResponse(requestId, operation, result) {
    if (requestId === refreshRequestId) {
      refreshRequestId = ""
      if (result && result.ok) syncFromService()
      else setStatus(String(result && result.error || "Could not refresh Choosium."), "error")
      return
    }
    if (requestId !== pendingRequestId) return

    busy = false
    pendingRequestId = ""
    if (!result || !result.ok) {
      syncFromService()
      setStatus(String(result && result.error || "The change could not be saved."), "error")
      return
    }

    syncFromService()
    if (pendingAction === "rule") {
      page = "dashboard"
      resetScroll()
    }
    var fallbackMessage = pendingAction === "fallback" ? "Default destination saved."
      : pendingAction === "move" ? "Route order saved."
      : pendingAction === "delete" ? "Route deleted."
      : pendingAction === "rule" ? "Route saved."
      : String(result.message || "Saved.")
    setStatus(fallbackMessage, "success")
    pendingAction = ""
  }

  onOpenedChanged: {
    if (opened) {
      syncFromService()
      refresh()
    }
  }

  onServiceChanged: {
    syncFromService()
    if (opened) refresh()
  }

  onEditorNameChanged: {
    if (ruleNameField && ruleNameField.text !== editorName)
      ruleNameField.text = editorName
  }

  onEditorClientsChanged: {
    if (!clientPicker) return
    var current = JSON.stringify(clientPicker.values || [])
    var next = JSON.stringify(editorClients || [])
    if (current !== next) clientPicker.values = Model.uniqueStrings(editorClients)
  }

  Connections {
    target: root.service
    function onResponse(requestId, operation, result) {
      root.handleResponse(requestId, operation, result)
    }
    function onConfigChanged() {
      if (!root.busy && root.page === "dashboard") root.syncFromService()
    }
    function onReadyChanged() {
      if (root.service && root.service.ready && !root.busy && root.page === "dashboard")
        root.syncFromService()
    }
  }

  Ui.KeyboardPanel {
    id: popup
    anchorItem: root.anchorItem
    owner: root.host || root
    bar: root.bar
    open: root.opened
    centerOnBar: true
    focusTarget: panelFocus
    contentWidth: popup.fittedContentWidth(C.Style.space(620))
    contentHeight: popup.fittedContentHeight(contentColumn.implicitHeight, C.Style.space(760))

    FocusScope {
      id: panelFocus
      anchors.fill: parent
      focus: true

      Keys.priority: deleteDialog.opened ? Keys.BeforeItem : Keys.AfterItem
      Keys.onPressed: function(event) {
        if (deleteDialog.opened && deleteDialog.handleKey(event)) {
          event.accepted = true
        } else if (event.key === Qt.Key_Escape) {
          if (root.page === "editor") root.leaveEditor()
          else root.close()
          event.accepted = true
        }
      }

      Flickable {
        id: panelScroll
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.implicitHeight
        flickableDirection: Flickable.VerticalFlick
        boundsBehavior: Flickable.StopAtBounds
        interactive: contentHeight > height
        clip: true

        QQC.ScrollBar.vertical: QQC.ScrollBar {
          policy: QQC.ScrollBar.AsNeeded
        }

        Column {
          id: contentColumn
          width: panelScroll.width
          spacing: C.Style.spacing.panelGap

          Row {
            id: headerRow
            width: parent.width
            spacing: C.Style.space(12)

            Ui.BorderSurface {
              id: brandMark
              width: C.Style.space(52)
              height: width
              radius: C.Style.cornerRadius
              color: C.Style.selectedFillFor(root.foreground, root.accent)
              borderSpec: C.Border.controlSpec("selected", root.foreground, root.accent)

              Text {
                anchors.centerIn: parent
                text: "󰖟"
                color: root.accent
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.display
              }
            }

            Column {
              width: headerRow.width - brandMark.width - defaultBadge.width - headerRow.spacing * 2
              anchors.verticalCenter: brandMark.verticalCenter
              spacing: C.Style.spacing.xs

              Text {
                width: parent.width
                text: "CHOOSIUM"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.heading
                font.bold: true
                font.letterSpacing: 1.4
                elide: Text.ElideRight
              }

              Text {
                width: parent.width
                text: "Every link, in the right browser."
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                elide: Text.ElideRight
              }
            }

            Ui.BorderSurface {
              id: defaultBadge
              width: defaultBadgeText.implicitWidth + C.Style.space(16)
              height: defaultBadgeText.implicitHeight + C.Style.space(10)
              anchors.verticalCenter: brandMark.verticalCenter
              radius: C.Style.cornerRadius
              color: root.routingHealthy
                ? C.Style.selectedFillFor(root.foreground, root.accent)
                : (root.handlerNeedsRepair
                  ? C.Style.normalFillFor(root.urgent, root.urgent)
                  : C.Style.normalFillFor(root.foreground, root.accent))
              borderSpec: C.Border.controlSpec(
                root.routingHealthy ? "selected" : "normal",
                root.handlerNeedsRepair ? root.urgent : root.foreground,
                root.handlerNeedsRepair ? root.urgent : root.accent)

              Text {
                id: defaultBadgeText
                anchors.centerIn: parent
                text: root.routingHealthy ? "ROUTING"
                  : (root.handlerNeedsRepair ? "REPAIR" : "STANDBY")
                color: root.routingHealthy ? root.accent
                  : (root.handlerNeedsRepair ? root.urgent : root.muted)
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.caption
                font.bold: true
                font.letterSpacing: 0.8
              }
            }
          }

          Ui.PanelSeparator {
            width: parent.width
            foreground: root.foreground
          }

          Column {
            id: dashboard
            visible: root.page === "dashboard"
            width: parent.width
            spacing: C.Style.spacing.panelGap

            Ui.BorderSurface {
              visible: !root.serviceReady
              width: parent.width
              height: serviceStateText.implicitHeight + C.Style.space(20)
              radius: C.Style.cornerRadius
              color: root.service && root.service.initializationError !== ""
                ? C.Style.normalFillFor(root.urgent, root.urgent)
                : C.Style.normalFillFor(root.foreground, root.accent)
              borderSpec: C.Border.controlSpec(
                root.service && root.service.initializationError !== "" ? "hover-cursor" : "normal",
                root.service && root.service.initializationError !== "" ? root.urgent : root.foreground,
                root.service && root.service.initializationError !== "" ? root.urgent : root.accent)

              Text {
                id: serviceStateText
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: C.Style.space(10)
                anchors.rightMargin: C.Style.space(10)
                text: root.service && root.service.initializationError !== ""
                  ? root.service.initializationError
                  : "Loading browsers and routing configuration..."
                color: root.service && root.service.initializationError !== ""
                  ? root.urgent : root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
            }

            Ui.BorderSurface {
              width: parent.width
              height: systemColumn.implicitHeight + C.Style.space(28)
              radius: C.Style.cornerRadius
              color: C.Style.normalFillFor(root.foreground, root.accent)
              borderSpec: C.Border.controlSpec("normal", root.foreground, root.accent)

              Column {
                id: systemColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: C.Style.space(14)
                anchors.rightMargin: C.Style.space(14)
                spacing: C.Style.space(10)

                Grid {
                  id: systemHeading
                  width: parent.width
                  height: childrenRect.height
                  columns: width >= C.Style.space(340) ? 2 : 1
                  columnSpacing: C.Style.space(10)
                  rowSpacing: C.Style.space(4)

                  Text {
                    id: systemTitle
                    width: systemHeading.columns === 2
                      ? systemHeading.width * 0.55 - systemHeading.columnSpacing
                      : systemHeading.width
                    text: "DEFAULT BROWSER"
                    color: root.foreground
                    font.family: root.fontFamily
                    font.pixelSize: C.Style.font.subtitle
                    font.bold: true
                    font.letterSpacing: 0.8
                  }

                  Text {
                    id: systemState
                    width: systemHeading.columns === 2
                      ? systemHeading.width * 0.45
                      : systemHeading.width
                    text: root.routingHealthy ? "CHOOSIUM"
                      : (root.handlerNeedsRepair ? "CHOOSIUM NEEDS REPAIR"
                      : String(root.service && root.service.currentDefaultName || "NOT SET").toUpperCase()
                      )
                    textFormat: Text.PlainText
                    color: root.routingHealthy ? root.accent
                      : (root.handlerNeedsRepair ? root.urgent : root.muted)
                    font.family: root.fontFamily
                    font.pixelSize: C.Style.font.caption
                    font.bold: true
                    font.letterSpacing: 0.7
                    horizontalAlignment: systemHeading.columns === 2 ? Text.AlignRight : Text.AlignLeft
                    elide: Text.ElideRight
                  }
                }

                Text {
                  width: parent.width
                  text: root.routingHealthy
                    ? "Choosium is your default browser. It checks the routes below, then sends unmatched links to your default destination."
                    : (root.handlerNeedsRepair
                      ? "Choosium needs to be set as your default browser again."
                      : String(root.service && root.service.currentDefaultName || "Another browser") + " is currently your default browser.")
                  color: root.muted
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }

                BrowserChoices {
                  width: parent.width
                  enabled: !root.busy && root.browsers.length > 0
                  label: "NO MATCHES GO TO"
                  value: root.fallbackBrowser
                  options: root.browsers
                  onChanged: function(value) { root.changeFallback(value) }
                }

                Flow {
                  width: parent.width
                  height: childrenRect.height
                  spacing: C.Style.spacing.rowGap

                  Ui.Button {
                    visible: root.serviceReady && !root.routingHealthy
                    text: root.busy ? "Working..."
                      : "Set Choosium as your Default browser"
                    iconText: root.busy ? "\uf110" : "\uf0c1"
                    iconSpinning: root.busy
                    bordered: true
                    selected: true
                    focusable: true
                    enabled: !root.busy && root.fallbackBrowser !== ""
                    opacity: enabled ? 1 : 0.45
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.makeDefault()
                  }

                  Ui.Button {
                    text: "Refresh"
                    iconText: "\uf2f1"
                    bordered: true
                    focusable: true
                    enabled: !root.busy
                    opacity: enabled ? 1 : 0.45
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.refresh()
                  }
                }
              }
            }

            Ui.BorderSurface {
              visible: root.service && root.service.legacyConfig
              width: parent.width
              height: migrationText.implicitHeight + C.Style.space(20)
              radius: C.Style.cornerRadius
              color: C.Style.selectedFillFor(root.foreground, root.accent)
              borderSpec: C.Border.controlSpec("selected", root.foreground, root.accent)

              Text {
                id: migrationText
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: C.Style.space(10)
                anchors.rightMargin: C.Style.space(10)
                text: "Hyprchoosy config detected. Your existing routes are loaded and will move to Choosium when you save or make it the default."
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                wrapMode: Text.WordWrap
              }
            }

            Item {
              width: parent.width
              height: Math.max(routesTitle.implicitHeight, addRouteButton.implicitHeight)

              Text {
                id: routesTitle
                anchors.left: parent.left
                anchors.right: addRouteButton.left
                anchors.rightMargin: C.Style.space(12)
                anchors.verticalCenter: parent.verticalCenter
                text: "ROUTES"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.subtitle
                font.bold: true
                font.letterSpacing: 0.8
                elide: Text.ElideRight
              }

              Ui.Button {
                id: addRouteButton
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: "Add route"
                iconText: "+"
                bordered: true
                selected: true
                focusable: true
                enabled: !root.busy && root.browsers.length > 0
                opacity: enabled ? 1 : 0.45
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.beginAdd()
              }
            }

            Text {
              width: parent.width
              text: "App routes win over website routes. Earlier matches win."
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              wrapMode: Text.WordWrap
            }

            Ui.BorderSurface {
              visible: root.rules.length === 0
              width: parent.width
              height: emptyColumn.implicitHeight + C.Style.space(36)
              radius: C.Style.cornerRadius
              color: "transparent"
              borderSpec: C.Border.controlSpec("normal", root.foreground, root.accent)

              Column {
                id: emptyColumn
                anchors.centerIn: parent
                width: parent.width - C.Style.space(48)
                spacing: C.Style.space(8)

                Text {
                  anchors.horizontalCenter: parent.horizontalCenter
                  text: "\uf074"
                  color: root.accent
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.display
                }

                Text {
                  width: parent.width
                  text: "No routes yet"
                  horizontalAlignment: Text.AlignHCenter
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.subtitle
                  font.bold: true
                }

                Text {
                  width: parent.width
                  text: "Add one and pick from the apps that are open right now."
                  horizontalAlignment: Text.AlignHCenter
                  color: root.muted
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.bodySmall
                  wrapMode: Text.WordWrap
                }
              }
            }

            Repeater {
              model: root.rules

              delegate: RuleCard {
                required property var modelData
                required property int index
                rule: modelData
                ruleIndex: index
              }
            }

            Text {
              visible: root.serviceReady
              width: parent.width
              text: "Config: " + String(root.service && root.service.sourcePath || "")
              textFormat: Text.PlainText
              color: root.muted
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.caption
              elide: Text.ElideMiddle
            }
          }

          Column {
            id: editor
            visible: root.page === "editor"
            width: parent.width
            spacing: C.Style.spacing.panelGap

            Item {
              width: parent.width
              height: Math.max(backButton.implicitHeight, editorHeading.implicitHeight)

              Ui.Button {
                id: backButton
                anchors.left: parent.left
                anchors.verticalCenter: parent.verticalCenter
                text: "Back"
                iconText: "\uf060"
                bordered: true
                focusable: true
                enabled: !root.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.leaveEditor()
              }

              Column {
                id: editorHeading
                anchors.left: backButton.right
                anchors.leftMargin: C.Style.space(14)
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: C.Style.spacing.xs

                Text {
                  width: parent.width
                  text: root.editingIndex < 0 ? "NEW ROUTE" : "EDIT ROUTE"
                  color: root.foreground
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.subtitle
                  font.bold: true
                  font.letterSpacing: 0.9
                }

                Text {
                  width: parent.width
                  text: "Choose what should match, then where its links should open."
                  color: root.muted
                  font.family: root.fontFamily
                  font.pixelSize: C.Style.font.caption
                  elide: Text.ElideRight
                }
              }
            }

            Column {
              width: parent.width
              spacing: C.Style.space(8)

              Text {
                text: "ROUTE NAME"
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.caption
                font.bold: true
                font.letterSpacing: 0.8
              }

              Ui.TextField {
                id: ruleNameField
                width: parent.width
                enabled: !root.busy
                text: root.editorName
                placeholderText: "Work links"
                maximumLength: 80
                foreground: root.foreground
                accent: root.accent
                font.family: root.fontFamily
                onTextEdited: root.editorName = text
                onActiveFocusChanged: if (activeFocus) root.reveal(this)
              }
            }

            BrowserChoices {
              width: parent.width
              enabled: !root.busy
              label: "OPEN IN"
              value: root.editorBrowser
              options: root.browsers
              onChanged: function(value) { root.editorBrowser = value }
            }

            Ui.PanelSeparator {
              width: parent.width
              foreground: root.foreground
            }

            Column {
              width: parent.width
              spacing: C.Style.space(8)

              Text {
                text: "SOURCE APPS"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.subtitle
                font.bold: true
                font.letterSpacing: 0.8
              }

              Text {
                width: parent.width
                text: "Open the picker to see windows reported by hyprctl right now. Select one or several app classes."
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                wrapMode: Text.WordWrap
              }

              Ui.MultiSelect {
                id: clientPicker
                width: parent.width
                enabled: !root.busy
                showLabel: false
                values: root.editorClients
                options: Model.optionsForValues(root.editorClients)
                optionsCommand: root.helperPath !== ""
                  ? ["python3", root.helperPath, "client-options"]
                  : []
                optionsCommandCwd: root.service ? root.service.pluginDir : ""
                placeholderText: "Search open apps..."
                emptyText: "No open Hyprland clients found"
                noSelectionText: "Choose from open apps"
                triggerLabel: "Choose from open apps"
                foreground: root.foreground
                accent: root.accent
                fontFamily: root.fontFamily
                onChanged: function(values) {
                  root.editorClients = values
                  if (!root.editorName && values.length > 0)
                    root.editorName = Model.titleFromClient(values[0])
                }
              }

              Grid {
                id: clientEntryGrid
                width: parent.width
                height: childrenRect.height
                columns: width >= C.Style.space(360) ? 2 : 1
                columnSpacing: C.Style.spacing.rowGap
                rowSpacing: C.Style.spacing.rowGap

                Ui.TextField {
                  id: clientField
                  width: clientEntryGrid.columns === 2
                    ? clientEntryGrid.width - addClientButton.implicitWidth - clientEntryGrid.columnSpacing
                    : clientEntryGrid.width
                  enabled: !root.busy
                  placeholderText: "Or type an app class, e.g. slack"
                  foreground: root.foreground
                  accent: root.accent
                  font.family: root.fontFamily
                  onAccepted: root.addManualClient()
                  onActiveFocusChanged: if (activeFocus) root.reveal(this)
                }

                Ui.Button {
                  id: addClientButton
                  width: clientEntryGrid.columns === 2 ? implicitWidth : clientEntryGrid.width
                  text: "Add app"
                  bordered: true
                  focusable: true
                  enabled: !root.busy && clientField.text.trim() !== ""
                  opacity: enabled ? 1 : 0.45
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.addManualClient()
                }
              }

              Flow {
                visible: root.editorClients.length > 0
                width: parent.width
                height: childrenRect.height
                spacing: C.Style.spacing.md

                Repeater {
                  model: root.editorClients

                  Ui.Button {
                    required property string modelData
                    text: "x  " + Model.plainText(modelData)
                    tooltipText: "Remove " + modelData
                    bordered: true
                    focusable: true
                    enabled: !root.busy
                    horizontalPadding: C.Style.space(8)
                    verticalPadding: C.Style.space(4)
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.editorClients = Model.removeValue(root.editorClients, modelData)
                  }
                }
              }
            }

            Ui.PanelSeparator {
              width: parent.width
              foreground: root.foreground
            }

            Column {
              width: parent.width
              spacing: C.Style.space(8)

              Text {
                text: "WEBSITES (OPTIONAL)"
                color: root.foreground
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.subtitle
                font.bold: true
                font.letterSpacing: 0.8
              }

              Text {
                width: parent.width
                text: "A domain also matches all of its subdomains. App matches still take priority."
                color: root.muted
                font.family: root.fontFamily
                font.pixelSize: C.Style.font.bodySmall
                wrapMode: Text.WordWrap
              }

              Grid {
                id: domainEntryGrid
                width: parent.width
                height: childrenRect.height
                columns: width >= C.Style.space(360) ? 2 : 1
                columnSpacing: C.Style.spacing.rowGap
                rowSpacing: C.Style.spacing.rowGap

                Ui.TextField {
                  id: domainField
                  width: domainEntryGrid.columns === 2
                    ? domainEntryGrid.width - addDomainButton.implicitWidth - domainEntryGrid.columnSpacing
                    : domainEntryGrid.width
                  enabled: !root.busy
                  placeholderText: "github.com"
                  foreground: root.foreground
                  accent: root.accent
                  font.family: root.fontFamily
                  inputMethodHints: Qt.ImhUrlCharactersOnly | Qt.ImhNoPredictiveText
                  onAccepted: root.addDomain()
                  onActiveFocusChanged: if (activeFocus) root.reveal(this)
                }

                Ui.Button {
                  id: addDomainButton
                  width: domainEntryGrid.columns === 2 ? implicitWidth : domainEntryGrid.width
                  text: "Add site"
                  bordered: true
                  focusable: true
                  enabled: !root.busy && domainField.text.trim() !== ""
                  opacity: enabled ? 1 : 0.45
                  foreground: root.foreground
                  fontFamily: root.fontFamily
                  onClicked: root.addDomain()
                }
              }

              Flow {
                visible: root.editorDomains.length > 0
                width: parent.width
                height: childrenRect.height
                spacing: C.Style.spacing.md

                Repeater {
                  model: root.editorDomains

                  Ui.Button {
                    required property string modelData
                    text: "x  " + Model.plainText(modelData)
                    tooltipText: "Remove " + modelData
                    bordered: true
                    focusable: true
                    enabled: !root.busy
                    horizontalPadding: C.Style.space(8)
                    verticalPadding: C.Style.space(4)
                    foreground: root.foreground
                    fontFamily: root.fontFamily
                    onClicked: root.editorDomains = Model.removeValue(root.editorDomains, modelData)
                  }
                }
              }
            }

            Flow {
              width: parent.width
              height: childrenRect.height
              spacing: C.Style.spacing.rowGap

              Ui.Button {
                text: root.busy ? "Saving..." : "Save route"
                iconText: root.busy ? "\uf110" : "\uf00c"
                iconSpinning: root.busy
                bordered: true
                selected: true
                focusable: true
                enabled: !root.busy
                opacity: enabled ? 1 : 0.45
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.saveEditor()
              }

              Ui.Button {
                text: "Cancel"
                bordered: true
                focusable: true
                enabled: !root.busy
                foreground: root.foreground
                fontFamily: root.fontFamily
                onClicked: root.leaveEditor()
              }
            }
          }

          Ui.BorderSurface {
            id: statusSurface
            visible: root.statusText !== ""
            width: parent.width
            height: statusLabel.implicitHeight + C.Style.space(20)
            radius: C.Style.cornerRadius
            color: root.statusKind === "error"
              ? C.Style.normalFillFor(root.urgent, root.urgent)
              : C.Style.normalFillFor(root.foreground, root.accent)
            borderSpec: C.Border.controlSpec(
              root.statusKind === "error" ? "hover-cursor" : "normal",
              root.statusKind === "error" ? root.urgent : root.foreground,
              root.statusKind === "error" ? root.urgent : root.accent)

            Text {
              id: statusLabel
              anchors.left: parent.left
              anchors.right: parent.right
              anchors.verticalCenter: parent.verticalCenter
              anchors.leftMargin: C.Style.space(10)
              anchors.rightMargin: C.Style.space(10)
              text: (root.statusKind === "error" ? "!  " : ">  ") + root.statusText
              textFormat: Text.PlainText
              color: root.statusKind === "error" ? root.urgent : root.foreground
              font.family: root.fontFamily
              font.pixelSize: C.Style.font.bodySmall
              wrapMode: Text.WordWrap
            }
          }

          Item {
            width: 1
            height: C.Style.spacing.hairline
          }
        }
      }

      Ui.ConfirmDialog {
        id: deleteDialog
        anchors.fill: parent
        z: 20
        confirmText: "Delete"
        cancelText: "Cancel"
        foreground: root.foreground
        selectedText: root.accent
        fontFamily: root.fontFamily
        onCanceled: {
          opened = false
          root.deleteIndex = -1
        }
        onConfirmed: root.confirmDelete()
      }
    }
  }

  component BrowserChoices: Column {
    id: choices

    property string label: ""
    property string value: ""
    property var options: []

    signal changed(string value)

    spacing: C.Style.space(8)

    Text {
      visible: choices.label !== ""
      width: parent.width
      text: choices.label
      textFormat: Text.PlainText
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: C.Style.font.caption
      font.bold: true
      font.letterSpacing: 0.8
    }

    Flow {
      visible: choices.options && choices.options.length > 0
      width: parent.width
      height: childrenRect.height
      spacing: C.Style.spacing.md

      Repeater {
        model: choices.options || []

        Ui.Button {
          required property var modelData
          text: Model.plainText(modelData.label || modelData.value)
          tooltipText: Model.plainText(modelData.description || "")
          bordered: true
          selected: String(choices.value) === String(modelData.value || modelData.desktopId || "")
          focusable: true
          horizontalPadding: C.Style.space(9)
          verticalPadding: C.Style.space(5)
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: choices.changed(String(modelData.value || modelData.desktopId || ""))
          onActiveFocusChanged: if (activeFocus) root.reveal(this)
        }
      }
    }

    Text {
      visible: !choices.options || choices.options.length === 0
      width: parent.width
      text: "No installed browsers found"
      color: root.muted
      font.family: root.fontFamily
      font.pixelSize: C.Style.font.bodySmall
      font.italic: true
    }
  }

  component RuleCard: Ui.BorderSurface {
    id: card

    required property var rule
    required property int ruleIndex

    width: parent ? parent.width : implicitWidth
    height: cardColumn.implicitHeight + C.Style.space(24)
    radius: C.Style.cornerRadius
    color: C.Style.normalFillFor(root.foreground, root.accent)
    borderSpec: C.Border.controlSpec("normal", root.foreground, root.accent)

    Column {
      id: cardColumn
      anchors.left: parent.left
      anchors.right: parent.right
      anchors.verticalCenter: parent.verticalCenter
      anchors.leftMargin: C.Style.space(12)
      anchors.rightMargin: C.Style.space(12)
      spacing: C.Style.space(8)

      Item {
        width: parent.width
        height: Math.max(routeNumber.height, routeName.implicitHeight, destination.implicitHeight)

        Ui.BorderSurface {
          id: routeNumber
          anchors.left: parent.left
          anchors.verticalCenter: parent.verticalCenter
          width: C.Style.space(30)
          height: C.Style.space(26)
          radius: C.Style.cornerRadius
          color: C.Style.selectedFillFor(root.foreground, root.accent)
          borderSpec: C.Border.controlSpec("selected", root.foreground, root.accent)

          Text {
            anchors.centerIn: parent
            text: String(card.ruleIndex + 1).padStart(2, "0")
            color: root.accent
            font.family: root.fontFamily
            font.pixelSize: C.Style.font.caption
            font.bold: true
          }
        }

        Text {
          id: routeName
          anchors.left: routeNumber.right
          anchors.leftMargin: C.Style.space(10)
          anchors.right: destination.left
          anchors.rightMargin: C.Style.space(10)
          anchors.verticalCenter: parent.verticalCenter
          text: String(card.rule.name || "Unnamed route")
          textFormat: Text.PlainText
          color: root.foreground
          font.family: root.fontFamily
          font.pixelSize: C.Style.font.subtitle
          font.bold: true
          elide: Text.ElideRight
        }

        Text {
          id: destination
          anchors.right: parent.right
          anchors.verticalCenter: parent.verticalCenter
          width: Math.min(implicitWidth, parent.width * 0.45)
          text: "→  " + Model.browserLabel(card.rule.browser, root.browsers)
          textFormat: Text.PlainText
          color: root.accent
          font.family: root.fontFamily
          font.pixelSize: C.Style.font.bodySmall
          font.bold: true
          horizontalAlignment: Text.AlignRight
          elide: Text.ElideRight
        }
      }

      Text {
        width: parent.width
        text: Model.triggerSummary(card.rule)
        textFormat: Text.PlainText
        color: root.muted
        font.family: root.fontFamily
        font.pixelSize: C.Style.font.bodySmall
        wrapMode: Text.WordWrap
      }

      Flow {
        width: parent.width
        height: childrenRect.height
        spacing: C.Style.spacing.md

        Ui.Button {
          text: "Up"
          bordered: true
          focusable: true
          enabled: !root.busy && card.ruleIndex > 0
          opacity: enabled ? 1 : 0.35
          horizontalPadding: C.Style.space(8)
          verticalPadding: C.Style.space(4)
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.moveRule(card.ruleIndex, -1)
        }

        Ui.Button {
          text: "Down"
          bordered: true
          focusable: true
          enabled: !root.busy && card.ruleIndex < root.rules.length - 1
          opacity: enabled ? 1 : 0.35
          horizontalPadding: C.Style.space(8)
          verticalPadding: C.Style.space(4)
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.moveRule(card.ruleIndex, 1)
        }

        Ui.Button {
          text: "Edit"
          bordered: true
          focusable: true
          enabled: !root.busy
          horizontalPadding: C.Style.space(8)
          verticalPadding: C.Style.space(4)
          foreground: root.foreground
          fontFamily: root.fontFamily
          onClicked: root.beginEdit(card.ruleIndex)
        }

        Ui.Button {
          text: "Delete"
          bordered: true
          focusable: true
          enabled: !root.busy
          horizontalPadding: C.Style.space(8)
          verticalPadding: C.Style.space(4)
          foreground: root.urgent
          accent: root.urgent
          fontFamily: root.fontFamily
          onClicked: root.askDelete(card.ruleIndex)
        }
      }
    }
  }
}
