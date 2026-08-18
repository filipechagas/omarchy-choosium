function clone(value) {
  return JSON.parse(JSON.stringify(value === undefined ? null : value))
}

function asArray(values) {
  if (Array.isArray(values)) return values
  if (!values || typeof values === "string" || typeof values.length !== "number") return []
  var length = Math.floor(values.length)
  if (!isFinite(length) || length < 0) return []
  var result = []
  for (var i = 0; i < length; i++) result.push(values[i])
  return result
}

function cloneRules(rules) {
  return clone(asArray(rules))
}

function browserLabel(identifier, browsers) {
  var value = String(identifier || "")
  var options = asArray(browsers)
  for (var i = 0; i < options.length; i++) {
    if (String(options[i].value || options[i].desktopId || "") === value)
      return plainText(options[i].label || value)
  }
  return plainText(value) || "Not configured"
}

function plainText(value) {
  return String(value || "").replace(/[<>]/g, " ").replace(/\s+/g, " ").trim()
}

function uniqueStrings(values) {
  var source = asArray(values)
  var result = []
  var seen = {}
  for (var i = 0; i < source.length; i++) {
    var value = String(source[i] || "").trim()
    var key = ":" + value.toLowerCase()
    if (!value || seen[key]) continue
    seen[key] = true
    result.push(value)
  }
  return result
}

function addUnique(values, value) {
  return uniqueStrings(asArray(values).concat([value]))
}

function removeValue(values, value) {
  var source = asArray(values)
  var key = String(value || "").toLowerCase()
  var result = []
  for (var i = 0; i < source.length; i++) {
    if (String(source[i]).toLowerCase() !== key) result.push(String(source[i]))
  }
  return result
}

function optionsForValues(values) {
  var source = uniqueStrings(values)
  var result = []
  for (var i = 0; i < source.length; i++) {
    result.push({
      value: source[i],
      label: plainText(source[i]),
      description: "Configured app class"
    })
  }
  return result
}

function cleanDomain(value) {
  var text = String(value || "").trim().toLowerCase()
  var scheme = text.match(/^([a-z][a-z0-9+.-]*):\/\//)
  if (scheme && scheme[1] !== "http" && scheme[1] !== "https") return ""
  if (scheme) text = text.slice(scheme[0].length)
  text = text.split(/[/?#]/)[0]
  var at = text.lastIndexOf("@")
  if (at !== -1) text = text.slice(at + 1)
  text = text.replace(/^\*\./, "")
  if (text.charAt(0) === "[") {
    var bracket = text.indexOf("]")
    if (bracket === -1 || !/^(?::\d+)?$/.test(text.slice(bracket + 1))) return ""
    var address = text.slice(1, bracket)
    return /^[0-9a-f:.]+$/.test(address) ? address : ""
  }
  text = text.replace(/:\d+$/, "").replace(/^\.+|\.+$/g, "")
  if (!text || text.length > 253 || /\s|\.\.|:/.test(text) || !/^[a-z0-9.-]+$/.test(text))
    return ""
  var labels = text.split(".")
  for (var i = 0; i < labels.length; i++) {
    if (!labels[i] || labels[i].length > 63 || /^-|-$/.test(labels[i])) return ""
  }
  if (/^[0-9.]+$/.test(text)) {
    if (labels.length !== 4) return ""
    for (var j = 0; j < labels.length; j++) {
      if (!/^\d{1,3}$/.test(labels[j]) || Number(labels[j]) > 255) return ""
    }
  }
  return text
}

function titleFromClient(value) {
  var text = String(value || "").trim().replace(/[-_.]+/g, " ")
  if (!text) return ""
  return text.split(/\s+/).map(function(part) {
    return part.charAt(0).toUpperCase() + part.slice(1)
  }).join(" ")
}

function ruleError(rule, rules, editingIndex) {
  var candidate = rule || {}
  var name = String(candidate.name || "").trim()
  var browser = String(candidate.browser || "").trim()
  var clients = uniqueStrings(candidate.clients)
  var domains = uniqueStrings(candidate.url)
  if (!name) return "Give this route a name."
  if (!browser) return "Choose a destination browser."
  if (clients.length === 0 && domains.length === 0)
    return "Choose an open app or add a website."
  for (var d = 0; d < domains.length; d++) {
    if (!cleanDomain(domains[d])) return "Enter valid HTTP or HTTPS website domains."
  }

  var source = asArray(rules)
  for (var i = 0; i < source.length; i++) {
    if (i !== editingIndex && String(source[i].name || "").toLowerCase() === name.toLowerCase())
      return "Another route already uses that name."
  }
  return ""
}

function upsertRule(rules, index, rule) {
  var result = cloneRules(rules)
  var normalized = {
    name: String(rule.name || "").trim(),
    browser: String(rule.browser || "").trim(),
    clients: uniqueStrings(rule.clients),
    url: uniqueStrings(rule.url).map(cleanDomain).filter(function(value) { return value !== "" })
  }
  if (index >= 0 && index < result.length) result[index] = normalized
  else result.push(normalized)
  return result
}

function removeRule(rules, index) {
  var result = cloneRules(rules)
  if (index >= 0 && index < result.length) result.splice(index, 1)
  return result
}

function moveRule(rules, index, direction) {
  var result = cloneRules(rules)
  var target = index + direction
  if (index < 0 || index >= result.length || target < 0 || target >= result.length)
    return result
  var item = result[index]
  result[index] = result[target]
  result[target] = item
  return result
}

function triggerSummary(rule) {
  var clients = uniqueStrings(rule && rule.clients).map(plainText)
  var domains = uniqueStrings(rule && rule.url).map(plainText)
  var parts = []
  if (clients.length > 0) parts.push("Apps: " + clients.join(", "))
  if (domains.length > 0) parts.push("Sites: " + domains.join(", "))
  return parts.join("  ·  ") || "No triggers"
}

if (typeof module !== "undefined") {
  module.exports = {
    addUnique: addUnique,
    browserLabel: browserLabel,
    cleanDomain: cleanDomain,
    cloneRules: cloneRules,
    moveRule: moveRule,
    optionsForValues: optionsForValues,
    plainText: plainText,
    removeRule: removeRule,
    removeValue: removeValue,
    ruleError: ruleError,
    titleFromClient: titleFromClient,
    triggerSummary: triggerSummary,
    uniqueStrings: uniqueStrings,
    upsertRule: upsertRule
  }
}
