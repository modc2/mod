import { Empty } from '@/components/ui';

export default function NotFound() {
  return <Empty>Nothing here. <a className="hash" href={`${process.env.NEXT_PUBLIC_BASE || '/starknet'}/`}>Back to the explorer</a></Empty>;
}
