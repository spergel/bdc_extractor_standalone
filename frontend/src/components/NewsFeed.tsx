import { useState, useEffect } from 'react';

type NewsItem = {
  id: string;
  title: string;
  source: string;
  publishedAt: string;
  url?: string;
  description?: string;
};

type Props = {
  ticker?: string;
  limit?: number;
};

export function NewsFeed({ ticker, limit = 10 }: Props) {
  const [news, setNews] = useState<NewsItem[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) {
      setNews([]);
      return;
    }

    setIsLoading(true);
    setError(null);

    // TODO: Replace with actual API endpoint
    // For now, we'll create a placeholder that can be connected to a news API
    // Options: NewsAPI, Alpha Vantage News, or custom backend endpoint
    const fetchNews = async () => {
      try {
        // Placeholder - replace with actual API call
        // Example: const response = await fetch(`/api/news/${ticker}`);
        // const data = await response.json();
        
        // Simulated delay
        await new Promise(resolve => setTimeout(resolve, 500));
        
        // Placeholder data structure
        const placeholderNews: NewsItem[] = [];
        
        setNews(placeholderNews);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load news');
        setNews([]);
      } finally {
        setIsLoading(false);
      }
    };

    fetchNews();
  }, [ticker]);

  if (!ticker) {
    return (
      <div className="window p-4">
        <div className="text-xs text-[#808080]">Select a BDC to view news</div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="window p-4">
        <div className="text-xs text-[#ff0000]">Error loading news: {error}</div>
      </div>
    );
  }

  return (
    <div className="window p-3">
      <div className="titlebar mb-2">
        <div className="text-sm font-semibold text-white">News Feed - {ticker}</div>
      </div>
      
      {isLoading ? (
        <div className="text-xs text-[#808080] p-4">Loading news...</div>
      ) : news.length === 0 ? (
        <div className="text-xs text-[#808080] p-4">
          No news available. News feed integration coming soon.
        </div>
      ) : (
        <div className="space-y-2 max-h-96 overflow-y-auto">
          {news.slice(0, limit).map((item) => (
            <div
              key={item.id}
              className="border-b border-[#c0c0c0] pb-2 last:border-b-0"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  {item.url ? (
                    <a
                      href={item.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-xs text-[#0000ff] hover:underline font-medium block mb-1"
                    >
                      {item.title}
                    </a>
                  ) : (
                    <div className="text-xs text-black font-medium mb-1">
                      {item.title}
                    </div>
                  )}
                  {item.description && (
                    <div className="text-xs text-[#808080] mb-1 line-clamp-2">
                      {item.description}
                    </div>
                  )}
                  <div className="flex items-center gap-2 text-[11px] text-[#808080]">
                    <span>{item.source}</span>
                    <span>•</span>
                    <span>
                      {new Date(item.publishedAt).toLocaleDateString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        year: 'numeric',
                      })}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
      
      {news.length > limit && (
        <div className="mt-3 pt-2 border-t border-[#c0c0c0] text-center">
          <button className="btn text-xs">
            Load More ({news.length - limit} remaining)
          </button>
        </div>
      )}
    </div>
  );
}



































