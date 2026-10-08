// Windows-only build patch for OpenCode 1.18.32 / y18n 5.0.8.
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const originalReader = `    _readLocaleFile() {
        let localeLookup = {};
        const languageFile = this._resolveLocaleFile(this.directory, this.locale);
        try {
            // When using a bundler such as webpack, readFileSync may not be defined:
            if (shim.fs.readFileSync) {
                localeLookup = JSON.parse(shim.fs.readFileSync(languageFile, 'utf-8'));
            }
        }
        catch (err) {
            if (err instanceof SyntaxError) {
                err.message = 'syntax error in ' + languageFile;
            }
            if (err.code === 'ENOENT')
                localeLookup = {};
            else
                throw err;
        }
        this.cache[this.locale] = localeLookup;
    }`;

export function rewriteLocaleReader(source, locales) {
  if (source.split(originalReader).length !== 2) {
    throw new Error("OpenCode locale patch requires the unmodified y18n 5.0.8 reader");
  }
  if (!Object.hasOwn(locales, "en")) throw new Error("Missing yargs English locale");
  const replacement = `    _readLocaleFile() {
        const language = this.locale.split('_')[0];
        const dictionary = Object.hasOwn(sonacodeEmbeddedLocales, this.locale)
            ? sonacodeEmbeddedLocales[this.locale]
            : this.fallbackToLanguage && Object.hasOwn(sonacodeEmbeddedLocales, language)
                ? sonacodeEmbeddedLocales[language] : {};
        this.cache[this.locale] = { ...dictionary };
    }`;
  return `// Sona Code: embedded CLI locales; no physical B: drive reads.\n`
    + `const sonacodeEmbeddedLocales = ${JSON.stringify(locales)};\n`
    + source.replace(originalReader, replacement);
}

let patched = 0;

export function assertLocalePatchApplied() {
  if (patched !== 1) throw new Error(`Expected one y18n locale module, patched ${patched}`);
}

export default {
  name: "sonacode-embedded-yargs-locales",
  setup(build) {
    const require = createRequire(path.join(process.cwd(), "package.json"));
    const yargsRoot = path.dirname(require.resolve("yargs"));
    const metadata = JSON.parse(fs.readFileSync(path.join(yargsRoot, "package.json"), "utf8"));
    if (metadata.version !== "18.0.0") throw new Error("Locale patch requires yargs 18.0.0");
    const directory = path.join(yargsRoot, "locales");
    const locales = Object.fromEntries(fs.readdirSync(directory).filter(file => file.endsWith(".json"))
      .sort().map(file => [file.slice(0, -5), JSON.parse(fs.readFileSync(path.join(directory, file), "utf8"))]));
    patched = 0;
    build.onLoad({ filter: /[\\/]y18n[\\/]build[\\/]lib[\\/]index\.js$/ }, async args => {
      patched++;
      return { contents: rewriteLocaleReader(await Bun.file(args.path).text(), locales), loader: "js" };
    });
  },
};
