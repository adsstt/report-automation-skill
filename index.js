import { cpSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, relative } from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'report-automation-skill'

const PACKAGE_ROOT = dirname(fileURLToPath(import.meta.url))
const SKILL_NAME = 'report-automation'
const SKILL_ENTRIES = [
  'SKILL.md',
  'README.md',
  'requirements.txt',
  'evals',
  'examples',
  'references',
  'scripts',
  'tests',
]

function isDistributable(sourceDir, sourcePath) {
  const parts = relative(sourceDir, sourcePath).split(/[\\/]+/)
  return !parts.some(part => part === 'runtime' || part === '__pycache__' || part === '.git')
    && !sourcePath.endsWith('.pyc')
}

/**
 * Copy the packaged skill into DSH's user skill library.
 * Existing runtime output is preserved because managed source entries are
 * updated in place instead of deleting the destination directory.
 */
export function syncSkill({ homeDir = homedir(), sourceDir = PACKAGE_ROOT } = {}) {
  const targetDir = join(homeDir, '.agents', 'skills', SKILL_NAME)
  mkdirSync(targetDir, { recursive: true })

  for (const entry of SKILL_ENTRIES) {
    cpSync(join(sourceDir, entry), join(targetDir, entry), {
      recursive: true,
      force: true,
      filter: sourcePath => isDistributable(sourceDir, sourcePath),
    })
  }

  const manifest = JSON.parse(readFileSync(join(sourceDir, 'package.json'), 'utf8'))
  writeFileSync(join(targetDir, '.dsh-source.json'), `${JSON.stringify({
    package: manifest.name,
    version: manifest.version,
    repository: manifest.repository?.url ?? null,
  }, null, 2)}\n`, 'utf8')

  return { targetDir, version: manifest.version }
}

export function apply(ctx) {
  const result = syncSkill()
  const logger = typeof ctx?.logger === 'function' ? ctx.logger(name) : null
  logger?.info?.(`installed report-automation skill ${result.version} to ${result.targetDir}`)
}
