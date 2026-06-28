import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import pkg from 'pg';
const { Client } = pkg;

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const ROOT = path.resolve(__dirname, '..');

// Load environment variables manually
function loadDotEnv() {
  const envPath = path.join(ROOT, '.env');
  if (!fs.existsSync(envPath)) return;
  const raw = fs.readFileSync(envPath, 'utf8');
  for (const line of raw.split(/\r?\n/)) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i <= 0) continue;
    const k = t.slice(0, i).trim();
    const v = t.slice(i + 1).trim().replace(/^['"]|['"]$/g, '');
    if (!(k in process.env)) process.env[k] = v;
  }
}
loadDotEnv();

const dbUrl = process.env.DATABASE_URL;
if (!dbUrl) {
  console.error('❌ Error: DATABASE_URL is not set in your .env file.');
  console.error('Example DATABASE_URL="postgresql://postgres:password@your-db.supabase.co:5432/postgres"');
  process.exit(1);
}

async function main() {
  const client = new Client({ connectionString: dbUrl });
  try {
    await client.connect();
    console.log('⚡ Connected successfully to Supabase PostgreSQL database.');

    // 1. Parse bist100-config.js to seed Companies
    const configPath = path.join(ROOT, 'bist100-config.js');
    if (!fs.existsSync(configPath)) {
      throw new Error(`bist100-config.js not found at: ${configPath}`);
    }
    const configText = fs.readFileSync(configPath, 'utf8');
    const codesMatch = configText.match(/window\.BIST100_CODES\s*=\s*(\[[\s\S]*?\]);/);
    if (!codesMatch) {
      throw new Error('Could not find window.BIST100_CODES in config file');
    }
    
    // Clean up code block to standard JSON
    const rawArrayText = codesMatch[1].replace(/\/\/.*$/gm, '').replace(/,(\s*[\]}])/g, '$1');
    const symbols = JSON.parse(rawArrayText);
    
    // Parse Fallback Sectors
    const sectorMatch = configText.match(/window\.BIST100_FALLBACK_SECTORS\s*=\s*(\{[\s\S]*?\});/);
    let sectors = {};
    if (sectorMatch) {
      const cleanedObj = sectorMatch[1]
        .replace(/\/\/.*$/gm, '')
        .replace(/,(\s*[\]}])/g, '$1')
        .replace(/([{,]\s*)([A-Za-z0-9_]+)(\s*:)/g, '$1"$2"$3');
      sectors = JSON.parse(cleanedObj);
    }

    // Parse Fallback Names
    const nameMatch = configText.match(/window\.BIST100_FALLBACK_NAMES\s*=\s*(\{[\s\S]*?\});/);
    let names = {};
    if (nameMatch) {
      const cleanedNamesObj = nameMatch[1]
        .replace(/\/\/.*$/gm, '')
        .replace(/,(\s*[\]}])/g, '$1')
        .replace(/([{,]\s*)([A-Za-z0-9_]+)(\s*:)/g, '$1"$2"$3');
      names = JSON.parse(cleanedNamesObj);
    }

    console.log(`🚀 Seeding ${symbols.length} companies into the database...`);
    for (const code of symbols) {
      const symbol = `${code}.IS`;
      const sector = sectors[code] || 'Unknown';
      const name = names[code] || code;

      await client.query(
        `INSERT INTO companies (symbol, name, sector) 
         VALUES ($1, $2, $3) 
         ON CONFLICT (symbol) DO UPDATE SET name = $2, sector = $3`,
        [symbol, name, sector]
      );
    }
    console.log('✅ Companies table populated.');

    // 2. Import Fundamentals Snapshot
    const fundPath = path.join(ROOT, 'data', 'fundamentals_snapshot.json');
    if (fs.existsSync(fundPath)) {
      const fundPayload = JSON.parse(fs.readFileSync(fundPath, 'utf8'));
      const fundData = fundPayload.data || {};
      const generatedAt = fundPayload.generatedAt || new Date().toISOString();
      const reportDate = generatedAt.split('T')[0];

      console.log(`🚀 Seeding fundamentals snapshot for ${Object.keys(fundData).length} symbols...`);
      for (const [key, metrics] of Object.entries(fundData)) {
        const symbol = key.endsWith('.IS') ? key : `${key}.IS`;
        await client.query(
          `INSERT INTO fundamentals (symbol, report_date, metrics) 
           VALUES ($1, $2, $3) 
           ON CONFLICT (symbol, report_date) DO UPDATE SET metrics = $3`,
          [symbol, reportDate, JSON.stringify(metrics)]
        );
      }
      console.log('✅ Fundamentals table populated.');
    } else {
      console.log('⚠️ No fundamentals_snapshot.json found, skipping.');
    }

    // 3. Import Signals JSON
    const signalsPath = path.join(ROOT, 'data', 'signals.json');
    if (fs.existsSync(signalsPath)) {
      const signalsPayload = JSON.parse(fs.readFileSync(signalsPath, 'utf8'));
      const generatedAt = signalsPayload.generatedAt || new Date().toISOString();
      const signalDate = generatedAt.split('T')[0];

      console.log(`🚀 Seeding signals history...`);

      // Import Buy Candidates
      const buys = signalsPayload.buy_candidates || [];
      for (const row of buys) {
        const symbol = row.symbol;
        await client.query(
          `INSERT INTO signals (symbol, signal_date, signal_type, score, risk_level, suggested_weight_pct, reason) 
           VALUES ($1, $2, 'buy', $3, $4, $5, $6)`,
          [symbol, signalDate, row.score, row.riskLevel, row.suggestedWeightPct, row.reason]
        );
      }

      // Import Sell Candidates
      const sells = signalsPayload.sell_candidates || [];
      for (const row of sells) {
        const symbol = row.symbol;
        await client.query(
          `INSERT INTO signals (symbol, signal_date, signal_type, score, risk_level, suggested_weight_pct, reason) 
           VALUES ($1, $2, 'sell', null, null, null, row.reason || 'Trailing Stop Hit')`,
          [symbol, signalDate]
        );
      }
      console.log('✅ Signals table populated.');
    } else {
      console.log('⚠️ No signals.json found, skipping.');
    }

    // 4. Import Active Positions
    const posPath = path.join(ROOT, 'data', 'scanner_positions.json');
    if (fs.existsSync(posPath)) {
      const posPayload = JSON.parse(fs.readFileSync(posPath, 'utf8'));
      const positions = posPayload.positions || {};
      
      console.log(`🚀 Seeding active tracked positions...`);
      for (const [key, state] of Object.entries(positions)) {
        const symbol = key.endsWith('.IS') ? key : `${key}.IS`;
        await client.query(
          `INSERT INTO scanner_positions (symbol, entry_price, highest_price, last_price, trailing_stop_price, z_score, atr14, entry_signal_at, last_updated_at) 
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
           ON CONFLICT (symbol) DO UPDATE SET 
             entry_price = EXCLUDED.entry_price,
             highest_price = EXCLUDED.highest_price,
             last_price = EXCLUDED.last_price,
             trailing_stop_price = EXCLUDED.trailing_stop_price,
             z_score = EXCLUDED.z_score,
             atr14 = EXCLUDED.atr14,
             last_updated_at = EXCLUDED.last_updated_at`,
          [
            symbol,
            state.entryPrice,
            state.highestPrice,
            state.lastPrice,
            state.trailingStopPrice || null,
            state.zScore || null,
            state.atr14 || null,
            state.entrySignalAt || new Date().toISOString(),
            state.lastUpdatedAt || new Date().toISOString()
          ]
        );
      }
      console.log('✅ Active tracked positions populated.');
    } else {
      console.log('⚠️ No scanner_positions.json found, skipping.');
    }

    console.log('🎉 Database seeding complete!');
  } catch (err) {
    console.error('❌ Migration failed:', err.message);
  } finally {
    await client.end();
  }
}

main();
