# Metrik Boşluk Raporu (BIST100, kap_truth)

- symbolCount: 100
- generatedAtMs: 1773223022710

## Özet

- `forwardPE`: boş `100` / 100 (coverage `%0.0`) | ana neden: KAP tarafında ileriye dönük tahmin verisi yok; bu alan modelde üretilmiyor. (100x)
- `fxNetPositionRatio`: boş `100` / 100 (coverage `%0.0`) | ana neden: KAP’ta net döviz pozisyon kalemi standart şekilde ayrışmadığı için oran boş. (100x)
- `pegRatio`: boş `41` / 100 (coverage `%59.0`) | ana neden: Kazanç büyümesi <= 0 olduğu için PEG bilinçli olarak boş bırakılıyor. (41x)
- `dividendYield`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (28x)
- `annualDividendPerShare`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `lastDividendPerShare`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `lastDividendDateMs`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `dividendPayoutPct`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `paidYears3y`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `regularityScore`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `eventCount`: boş `28` / 100 (coverage `%72.0`) | ana neden: KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi). (28x)
- `debtMaturityRatio`: boş `13` / 100 (coverage `%87.0`) | ana neden: Kısa vadeli borç kalemi boş olduğu için vade dağılımı oranı hesaplanamadı. (11x)
- `ebitda`: boş `12` / 100 (coverage `%88.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (12x)
- `netDebtToEbitda`: boş `12` / 100 (coverage `%88.0`) | ana neden: FAVÖK boş/0 olduğu için NetBorç/FAVÖK hesaplanamadı. (12x)
- `interestCoverage`: boş `12` / 100 (coverage `%88.0`) | ana neden: Finansman gideri boş/0 olduğu için faiz karşılama hesaplanamadı. (8x)
- `currentBorrowings`: boş `11` / 100 (coverage `%89.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (11x)
- `inventories`: boş `11` / 100 (coverage `%89.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (11x)
- `operatingMargins`: boş `10` / 100 (coverage `%90.0`) | ana neden: Gelir boş/0 olduğu için faaliyet marjı hesaplanamadı. (6x)
- `freeCashflow`: boş `10` / 100 (coverage `%90.0`) | ana neden: CFO boş olduğu için FCF hesaplanamadı. (7x)
- `nonCurrentBorrowings`: boş `10` / 100 (coverage `%90.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (10x)
- `depreciationAmortization`: boş `10` / 100 (coverage `%90.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (10x)
- `currentRatio`: boş `9` / 100 (coverage `%91.0`) | ana neden: Dönen varlıklar boş olduğu için cari oran hesaplanamadı. (9x)
- `quickRatio`: boş `9` / 100 (coverage `%91.0`) | ana neden: Dönen varlıklar boş olduğu için likit oran hesaplanamadı. (9x)
- `currentAssets`: boş `9` / 100 (coverage `%91.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (9x)
- `currentLiabilities`: boş `9` / 100 (coverage `%91.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (9x)
- `grossMargins`: boş `8` / 100 (coverage `%92.0`) | ana neden: Gelir boş/0 olduğu için brüt marj hesaplanamadı. (6x)
- `interestExpense`: boş `8` / 100 (coverage `%92.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (8x)
- `cfoToNetIncome`: boş `7` / 100 (coverage `%93.0`) | ana neden: CFO boş olduğu için CFO/NetKâr hesaplanamadı. (7x)
- `cfo`: boş `7` / 100 (coverage `%93.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (7x)
- `revenueGrowth`: boş `6` / 100 (coverage `%94.0`) | ana neden: Önceki dönem karşılaştırma baz kalemi KAP’ta çıkmadığı (veya 0 olduğu) için büyüme hesaplanamadı. (6x)
- `profitMargins`: boş `6` / 100 (coverage `%94.0`) | ana neden: Gelir boş/0 olduğu için net marj hesaplanamadı. (6x)
- `priceToSalesTrailing12Months`: boş `6` / 100 (coverage `%94.0`) | ana neden: Gelir (revenue) boş/0 olduğu için F/S hesaplanamadı. (6x)
- `assetTurnover`: boş `6` / 100 (coverage `%94.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (6x)
- `profitabilityStability`: boş `6` / 100 (coverage `%94.0`) | ana neden: ROE/profit marjı ve büyüme sinyalleri tam oluşmadığı için stabilite skoru üretilmedi. (6x)
- `growthStability`: boş `6` / 100 (coverage `%94.0`) | ana neden: Gelir ve kazanç büyümesi birlikte hesaplanamadığı için büyüme stabilitesi boş. (6x)
- `operatingProfit`: boş `4` / 100 (coverage `%96.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (4x)
- `capex`: boş `3` / 100 (coverage `%97.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (3x)
- `grossProfit`: boş `2` / 100 (coverage `%98.0`) | ana neden: KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi. (2x)

## Detay (Metrik Bazında)

### forwardPE
- boş: 100/100
- coverage: %0.0
- nedenler:
  - 100x KAP tarafında ileriye dönük tahmin verisi yok; bu alan modelde üretilmiyor.
    - örnek hisseler: AEFES, AGHOL, AKBNK, AKSA, AKSEN, ALARK, ALTNY, ANSGR, ARCLK, ASTOR
- boş olan hisseler:
  - AEFES, AGHOL, AKBNK, AKSA, AKSEN, ALARK, ALTNY, ANSGR, ARCLK, ASTOR, BALSU, BIMAS, BRSAN, BRYAT, BSOKE, BTCIM, CANTE, CCOLA, CIMSA, CWENE, DAPGM, DOAS, DOHOL, DSTKF, ECILC, EFOR, EGEEN, EKGYO, ENERY, ENJSA, ENKAI, EREGL, EUPWR, FENER, FROTO, GARAN, GENIL, GESAN, GLRMK, GRSEL, GRTHO, GSRAY, GUBRF, HALKB, HEKTS, ISCTR, ISMEN, IZENR, KCAER, KCHOL, KLRHO, KONTR, KRDMD, KTLEV, KUYAS, MAGEN, MAVI, MGROS, MIATK, MPARK, OBAMS, ODAS, OTKAR, OYAKC, PASEU, PATEK, PETKM, PGSUS, QUAGR, RALYH, REEDR, SAHOL, SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### fxNetPositionRatio
- boş: 100/100
- coverage: %0.0
- nedenler:
  - 100x KAP’ta net döviz pozisyon kalemi standart şekilde ayrışmadığı için oran boş.
    - örnek hisseler: AEFES, AGHOL, AKBNK, AKSA, AKSEN, ALARK, ALTNY, ANSGR, ARCLK, ASTOR
- boş olan hisseler:
  - AEFES, AGHOL, AKBNK, AKSA, AKSEN, ALARK, ALTNY, ANSGR, ARCLK, ASTOR, BALSU, BIMAS, BRSAN, BRYAT, BSOKE, BTCIM, CANTE, CCOLA, CIMSA, CWENE, DAPGM, DOAS, DOHOL, DSTKF, ECILC, EFOR, EGEEN, EKGYO, ENERY, ENJSA, ENKAI, EREGL, EUPWR, FENER, FROTO, GARAN, GENIL, GESAN, GLRMK, GRSEL, GRTHO, GSRAY, GUBRF, HALKB, HEKTS, ISCTR, ISMEN, IZENR, KCAER, KCHOL, KLRHO, KONTR, KRDMD, KTLEV, KUYAS, MAGEN, MAVI, MGROS, MIATK, MPARK, OBAMS, ODAS, OTKAR, OYAKC, PASEU, PATEK, PETKM, PGSUS, QUAGR, RALYH, REEDR, SAHOL, SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### pegRatio
- boş: 41/100
- coverage: %59.0
- nedenler:
  - 41x Kazanç büyümesi <= 0 olduğu için PEG bilinçli olarak boş bırakılıyor.
    - örnek hisseler: AEFES, AGHOL, ALARK, ARCLK, BSOKE, BTCIM, CANTE, CCOLA, CIMSA, DAPGM
- boş olan hisseler:
  - AEFES, AGHOL, ALARK, ARCLK, BSOKE, BTCIM, CANTE, CCOLA, CIMSA, DAPGM, DOAS, EFOR, EGEEN, EKGYO, EREGL, FROTO, GESAN, GLRMK, GSRAY, GUBRF, HEKTS, IZENR, KONTR, KRDMD, MGROS, MIATK, MPARK, ODAS, OYAKC, PETKM, QUAGR, REEDR, SASA, SKBNK, TAVHL, TKFEN, TTRAK, TUKAS, ZOREN, CVKMD, SOKM

### dividendYield
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### annualDividendPerShare
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### lastDividendPerShare
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### lastDividendDateMs
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### dividendPayoutPct
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### paidYears3y
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### regularityScore
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### eventCount
- boş: 28/100
- coverage: %72.0
- nedenler:
  - 28x KAP temettü olay kaydı bulunamadı (şirket dağıtmamış olabilir veya parse edilemedi).
    - örnek hisseler: SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT
- boş olan hisseler:
  - SASA, SISE, SKBNK, TABGD, TAVHL, TCELL, THYAO, TKFEN, TOASO, TRALT, TRENJ, TRMET, TSKB, TTKOM, TTRAK, TUKAS, TUPRS, TUREX, TURSG, ULKER, VAKBN, VESTL, YEOTK, YKBNK, ZOREN, ASELS, CVKMD, SOKM

### debtMaturityRatio
- boş: 13/100
- coverage: %87.0
- nedenler:
  - 11x Kısa vadeli borç kalemi boş olduğu için vade dağılımı oranı hesaplanamadı.
    - örnek hisseler: AKBNK, ANSGR, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN
  - 2x Uzun vadeli borç kalemi boş/0 olduğu için vade dağılımı oranı hesaplanamadı.
    - örnek hisseler: ASTOR, BRYAT
- boş olan hisseler:
  - AKBNK, ANSGR, ASTOR, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN, YKBNK

### ebitda
- boş: 12/100
- coverage: %88.0
- nedenler:
  - 12x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, ANSGR, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG
- boş olan hisseler:
  - AKBNK, ANSGR, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN, YKBNK

### netDebtToEbitda
- boş: 12/100
- coverage: %88.0
- nedenler:
  - 12x FAVÖK boş/0 olduğu için NetBorç/FAVÖK hesaplanamadı.
    - örnek hisseler: AKBNK, ANSGR, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG
- boş olan hisseler:
  - AKBNK, ANSGR, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN, YKBNK

### interestCoverage
- boş: 12/100
- coverage: %88.0
- nedenler:
  - 8x Finansman gideri boş/0 olduğu için faiz karşılama hesaplanamadı.
    - örnek hisseler: AKBNK, BRYAT, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
  - 4x Faaliyet kârı boş olduğu için faiz karşılama hesaplanamadı.
    - örnek hisseler: ANSGR, DSTKF, KTLEV, TURSG
- boş olan hisseler:
  - AKBNK, ANSGR, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN, YKBNK

### currentBorrowings
- boş: 11/100
- coverage: %89.0
- nedenler:
  - 11x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, ANSGR, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN
- boş olan hisseler:
  - AKBNK, ANSGR, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN, YKBNK

### inventories
- boş: 11/100
- coverage: %89.0
- nedenler:
  - 11x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TSKB, VAKBN
- boş olan hisseler:
  - AKBNK, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, TSKB, VAKBN, YKBNK

### operatingMargins
- boş: 10/100
- coverage: %90.0
- nedenler:
  - 6x Gelir boş/0 olduğu için faaliyet marjı hesaplanamadı.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
  - 4x Faaliyet kârı boş olduğu için faaliyet marjı hesaplanamadı.
    - örnek hisseler: ANSGR, DSTKF, KTLEV, TURSG
- boş olan hisseler:
  - AKBNK, ANSGR, DSTKF, HALKB, ISCTR, KTLEV, SKBNK, TURSG, VAKBN, YKBNK

### freeCashflow
- boş: 10/100
- coverage: %90.0
- nedenler:
  - 7x CFO boş olduğu için FCF hesaplanamadı.
    - örnek hisseler: AKBNK, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
  - 3x CapEx boş olduğu için FCF hesaplanamadı.
    - örnek hisseler: ANSGR, BRYAT, TURSG
- boş olan hisseler:
  - AKBNK, ANSGR, BRYAT, GARAN, HALKB, ISCTR, SKBNK, TURSG, VAKBN, YKBNK

### nonCurrentBorrowings
- boş: 10/100
- coverage: %90.0
- nedenler:
  - 10x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK

### depreciationAmortization
- boş: 10/100
- coverage: %90.0
- nedenler:
  - 10x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, BRYAT, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK

### currentRatio
- boş: 9/100
- coverage: %91.0
- nedenler:
  - 9x Dönen varlıklar boş olduğu için cari oran hesaplanamadı.
    - örnek hisseler: AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK

### quickRatio
- boş: 9/100
- coverage: %91.0
- nedenler:
  - 9x Dönen varlıklar boş olduğu için likit oran hesaplanamadı.
    - örnek hisseler: AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK

### currentAssets
- boş: 9/100
- coverage: %91.0
- nedenler:
  - 9x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK

### currentLiabilities
- boş: 9/100
- coverage: %91.0
- nedenler:
  - 9x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, DSTKF, GARAN, HALKB, ISCTR, KTLEV, SKBNK, VAKBN, YKBNK

### grossMargins
- boş: 8/100
- coverage: %92.0
- nedenler:
  - 6x Gelir boş/0 olduğu için brüt marj hesaplanamadı.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
  - 2x Brüt kâr boş olduğu için brüt marj hesaplanamadı.
    - örnek hisseler: ANSGR, TURSG
- boş olan hisseler:
  - AKBNK, ANSGR, HALKB, ISCTR, SKBNK, TURSG, VAKBN, YKBNK

### interestExpense
- boş: 8/100
- coverage: %92.0
- nedenler:
  - 8x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, BRYAT, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, BRYAT, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### cfoToNetIncome
- boş: 7/100
- coverage: %93.0
- nedenler:
  - 7x CFO boş olduğu için CFO/NetKâr hesaplanamadı.
    - örnek hisseler: AKBNK, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### cfo
- boş: 7/100
- coverage: %93.0
- nedenler:
  - 7x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, GARAN, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### revenueGrowth
- boş: 6/100
- coverage: %94.0
- nedenler:
  - 6x Önceki dönem karşılaştırma baz kalemi KAP’ta çıkmadığı (veya 0 olduğu) için büyüme hesaplanamadı.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### profitMargins
- boş: 6/100
- coverage: %94.0
- nedenler:
  - 6x Gelir boş/0 olduğu için net marj hesaplanamadı.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### priceToSalesTrailing12Months
- boş: 6/100
- coverage: %94.0
- nedenler:
  - 6x Gelir (revenue) boş/0 olduğu için F/S hesaplanamadı.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### assetTurnover
- boş: 6/100
- coverage: %94.0
- nedenler:
  - 6x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### profitabilityStability
- boş: 6/100
- coverage: %94.0
- nedenler:
  - 6x ROE/profit marjı ve büyüme sinyalleri tam oluşmadığı için stabilite skoru üretilmedi.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### growthStability
- boş: 6/100
- coverage: %94.0
- nedenler:
  - 6x Gelir ve kazanç büyümesi birlikte hesaplanamadığı için büyüme stabilitesi boş.
    - örnek hisseler: AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK
- boş olan hisseler:
  - AKBNK, HALKB, ISCTR, SKBNK, VAKBN, YKBNK

### operatingProfit
- boş: 4/100
- coverage: %96.0
- nedenler:
  - 4x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: ANSGR, DSTKF, KTLEV, TURSG
- boş olan hisseler:
  - ANSGR, DSTKF, KTLEV, TURSG

### capex
- boş: 3/100
- coverage: %97.0
- nedenler:
  - 3x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: ANSGR, BRYAT, TURSG
- boş olan hisseler:
  - ANSGR, BRYAT, TURSG

### grossProfit
- boş: 2/100
- coverage: %98.0
- nedenler:
  - 2x KAP snapshot’ta alan boş: ilgili finansal kalem kaynak tabloda bulunamadı veya eşleşmedi.
    - örnek hisseler: ANSGR, TURSG
- boş olan hisseler:
  - ANSGR, TURSG
