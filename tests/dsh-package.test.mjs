import assert from 'node:assert/strict'
import { existsSync, mkdtempSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')

test('repository is a loadable DSH package', () => {
  const manifest = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
  assert.equal(manifest.name, 'report-automation-skill')
  assert.equal(manifest.main, './index.js')
  assert.equal(manifest.dsh?.bundle?.patch, './cordis.patch.yml')
  assert.ok(existsSync(join(root, 'index.js')))
  assert.ok(existsSync(join(root, 'cordis.patch.yml')))
})

test('DSH entry installs the skill into the user skill library', async () => {
  const homeDir = mkdtempSync(join(tmpdir(), 'report-automation-dsh-'))
  const entry = await import(pathToFileURL(join(root, 'index.js')).href)
  const result = entry.syncSkill({ homeDir, sourceDir: root })
  const installed = join(homeDir, '.agents', 'skills', 'report-automation')

  assert.equal(result.targetDir, installed)
  assert.ok(existsSync(join(installed, 'SKILL.md')))
  assert.ok(existsSync(join(installed, 'scripts', 'generate_report.py')))
  assert.ok(existsSync(join(installed, 'scripts', 'create_standard_template.py')))
  assert.ok(existsSync(join(installed, 'references', 'report-profile-schema.json')))
  assert.ok(existsSync(join(installed, 'evals', 'fresh-product', 'run_acceptance.py')))
  assert.ok(existsSync(join(installed, 'evals', 'fresh-product', 'standard-sources', 'novafit_meta_ads.csv')))
  assert.equal(existsSync(join(installed, 'package.json')), false)
  assert.match(readFileSync(join(installed, 'SKILL.md'), 'utf8'), /name: report-automation/)
})
