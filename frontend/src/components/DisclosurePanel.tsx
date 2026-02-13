export function DisclosurePanel() {
  return (
    <div className="window p-4 text-xs sm:text-sm leading-relaxed text-black space-y-3">
      <div className="text-sm font-semibold mb-1">Important Disclosures</div>

      <p>
        This application uses automated tools, including large language models (LLMs), to scrape,
        normalize, and aggregate data from SEC filings and other sources. While we make a best
        effort to clean and validate the data, the results may contain errors, omissions, or
        misclassifications.
      </p>

      <p>
        <strong>No representation or warranty</strong>, express or implied, is made as to the
        accuracy, completeness, timeliness, or suitability of any information displayed here.
        Data may differ from official company filings and should not be relied upon as a sole
        source of truth.
      </p>

      <p>
        <strong>Not investment advice:</strong> Nothing in this application should be interpreted
        as investment, legal, tax, or financial advice, or as a recommendation to buy, sell, or
        hold any security. You are solely responsible for your own investment decisions.
      </p>

      <p>
        Always refer to the original SEC filings and official company disclosures for definitive
        information. If you discover discrepancies or obvious errors in the data, please treat the
        affected outputs as unreliable and cross-check against the source documents.
      </p>

      <p className="text-[#808080] mt-2">
        By using this tool, you acknowledge and agree that the data is experimental and may be
        inaccurate, and that you will not treat it as advice or as a substitute for your own
        independent analysis.
      </p>
    </div>
  );
}

