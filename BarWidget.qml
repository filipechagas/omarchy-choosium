import QtQuick
import qs.Ui as Ui

Ui.BarWidget {
  id: root
  moduleName: "io.github.filipechagas.choosium"

  readonly property bool showIcon: setting("showIcon", true) !== false
  readonly property var choosiumService: bar && bar.shell
    ? bar.shell.serviceFor(moduleName)
    : null
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item
    ? panelLoader.item.popoutSwitchClosing === true
    : false
  readonly property bool routingHealthy: choosiumService
    && choosiumService.ready
    && choosiumService.isDefault
    && choosiumService.desktopInstalled

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    if ("bar" in target) target.bar = root.bar
    if ("settings" in target) target.settings = root.settings
    if ("anchorItem" in target) target.anchorItem = button
    if ("host" in target) target.host = root
    if ("service" in target) target.service = root.choosiumService
  }

  function open() {
    if (panelLoader.item && panelLoader.item.open) panelLoader.item.open()
  }

  function close() {
    if (panelLoader.item && panelLoader.item.close) panelLoader.item.close()
  }

  function toggle() {
    if (panelLoader.item && panelLoader.item.toggle) panelLoader.item.toggle()
  }

  function closeForPopoutSwitch() {
    if (panelLoader.item && panelLoader.item.closeForPopoutSwitch)
      panelLoader.item.closeForPopoutSwitch()
  }

  implicitWidth: showIcon ? button.implicitWidth : 0
  implicitHeight: showIcon ? button.implicitHeight : 0

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()
  onChoosiumServiceChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  Ui.BarIconButton {
    id: button
    anchors.fill: parent
    visible: root.showIcon
    bar: root.bar
    text: "󰖟"
    active: root.opened
    dimmed: root.choosiumService && !root.routingHealthy
    tooltipText: {
      if (!root.choosiumService || !root.choosiumService.ready) return "Choosium"
      var routes = root.choosiumService.ruleCount
      var status = root.routingHealthy ? "routing links"
        : (root.choosiumService.isDefault ? "handler needs repair" : "not the default")
      return "Choosium - " + routes + (routes === 1 ? " route, " : " routes, ") + status
    }
    onPressed: root.toggle()
  }

}
