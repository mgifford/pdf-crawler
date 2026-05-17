const assert = require("node:assert/strict");
const path = require("node:path");
const { pathToFileURL } = require("node:url");
const { Given, When, Then } = require("@cucumber/cucumber");

function formUrl() {
  if (process.env.FORM_PAGE_URL) return process.env.FORM_PAGE_URL;
  const formPath = path.resolve(__dirname, "../../docs/index.html");
  return pathToFileURL(formPath).href;
}

Given("I open the PDF crawler form", async function () {
  await this.page.goto(formUrl());
});

When("I enter {string} in the site URL field", async function (url) {
  await this.page.fill("#site-url", url);
});

Then("I should see a validation message containing {string}", async function (text) {
  await this.page.waitForSelector("#validation-preview", { state: "visible" });
  const content = await this.page.locator("#validation-preview").innerText();
  assert.ok(
    content.includes(text),
    `Expected validation preview to include "${text}", but got "${content}"`
  );
});
