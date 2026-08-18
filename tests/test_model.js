const test = require("node:test")
const assert = require("node:assert/strict")
const Model = require("../Model.js")

test("upsertRule normalizes values without mutating the source", () => {
  const source = [{ name: "Existing", browser: "a.desktop", clients: ["app"], url: [] }]
  const result = Model.upsertRule(source, -1, {
    name: "  Docs  ",
    browser: "firefox.desktop",
    clients: ["Slack", "slack", ""],
    url: ["https://Docs.Example.com/path"]
  })

  assert.equal(source.length, 1)
  assert.equal(result.length, 2)
  assert.deepEqual(result[1], {
    name: "Docs",
    browser: "firefox.desktop",
    clients: ["Slack"],
    url: ["docs.example.com"]
  })
})

test("moveRule keeps ordered routing deterministic", () => {
  const rules = [{ name: "one" }, { name: "two" }, { name: "three" }]
  assert.deepEqual(Model.moveRule(rules, 2, -1).map(rule => rule.name), ["one", "three", "two"])
  assert.deepEqual(Model.moveRule(rules, 0, -1), rules)
})

test("ruleError requires a destination and at least one trigger", () => {
  assert.equal(Model.ruleError({ name: "", browser: "", clients: [], url: [] }, [], -1), "Give this route a name.")
  assert.equal(
    Model.ruleError({ name: "Work", browser: "firefox.desktop", clients: [], url: [] }, [], -1),
    "Choose an open app or add a website."
  )
})

test("browser labels and trigger summaries are human readable", () => {
  const browsers = [{ value: "firefox.desktop", label: "Firefox" }]
  assert.equal(Model.browserLabel("firefox.desktop", browsers), "Firefox")
  assert.equal(
    Model.triggerSummary({ clients: ["Slack"], url: ["example.com"] }),
    "Apps: Slack  ·  Sites: example.com"
  )
})

test("trigger summaries accept QML array-like rule values", () => {
  const clients = { 0: "thunderbird", length: 1 }
  const sites = { 0: "github.com", 1: "figma.com", length: 2 }

  assert.equal(
    Model.triggerSummary({ clients, url: sites }),
    "Apps: thunderbird  ·  Sites: github.com, figma.com"
  )
})

test("domain cleanup accepts web URLs and rejects malformed or non-web input", () => {
  assert.equal(Model.cleanDomain("https://User@Docs.Example.com:443/path"), "docs.example.com")
  assert.equal(Model.cleanDomain("ftp://example.com/file"), "")
  assert.equal(Model.cleanDomain("not a host"), "")
  assert.equal(Model.cleanDomain("-broken.example"), "")
})

test("display text cannot inject rich text", () => {
  assert.equal(Model.plainText('<img src="https://example.com"> Slack'), "img src=\"https://example.com\" Slack")
  assert.equal(
    Model.triggerSummary({ clients: ["<b>Slack</b>"], url: [] }),
    "Apps: b Slack /b"
  )
})
