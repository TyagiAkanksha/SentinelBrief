export type PaginationProps = {
  page: number;
  pageSize: number;
  total: number;
  hrefForPage: (page: number) => string;
};
