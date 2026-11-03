
#include "queue.h"

#define enter_critical_section()
#define exit_critical_section()
#define queue_debug(q)

extern void printf_msg(const char *format,...);

int queue_reset(queue_t *q)
{
	if (!q) return -1;

	//enter_critical_section();
	q->wr = q->base;
	q->rd = q->base;
	q->num_item = 0;
	q->ovf_item = 0;
	q->fifo_capacity = q->buffer_size / q->item_size;
#ifdef QUEUE_USAGE_STATS
	q->pop_cnt = 0;
	q->push_cnt = 0;
	q->stats_period_cnt = 100; // Default
#endif
	exit_critical_section();
	return 0;
}

int queue_update_items_size(queue_t *q, uint32_t item_size)
{
	if (!q || !item_size || item_size > q->max_buffer_size)
		return -1;

	enter_critical_section();
	queue_reset(q);
	q->item_size = item_size;
	q->buffer_size = q->max_buffer_size - (q->max_buffer_size % item_size);
	q->fifo_capacity = q->buffer_size / q->item_size;
	exit_critical_section();
	return 0;
}

int queue_len(queue_t *q)
{
	int num_elements;

	if (!q) return -1;

	enter_critical_section();
	num_elements = q->num_item;
	exit_critical_section();

	return num_elements;
}

int queue_capacity(queue_t *q)
{
	int capacity;

	if (!q)
		return -1;

	enter_critical_section();
	capacity = q->fifo_capacity;
	exit_critical_section();

	return capacity;
}

int queue_buffer_size(queue_t *q)
{
	if (!q)
		return -1;
	return q->buffer_size;
}

int queue_init(queue_t *q, void *buf, uint32_t item_size, uint32_t buffer_size)
{
	if (!q || !buf || !buffer_size || !item_size || buffer_size < item_size)
		return -1;

	if (buffer_size % item_size != 0)
		return -1; // Padding problem

	enter_critical_section();
	q->num_item = 0;
	q->ovf_item = 0;
	q->base = (char *)buf;
	q->wr = (char *)buf;
	q->rd = (char *)buf;
	q->item_size = item_size;
	q->buffer_size = buffer_size;
	q->max_buffer_size = buffer_size;
	q->name = NULL;
	q->fifo_capacity = q->buffer_size / q->item_size;
#ifdef QUEUE_USAGE_STATS
	q->pop_cnt = 0;
	q->push_cnt = 0;
	q->stats_period_cnt = 100; // Default
#endif
	exit_critical_section();
	return 0;
}

int queue_init_by_name(queue_t *q, void *buf, uint32_t item_size, uint32_t buffer_size, const char *name)
{
	int ret = queue_init(q, buf, item_size, buffer_size);
	if (ret < 0)
		return ret;

	q->name = (char *)name;
	return 0;
}


void queue_destroy(queue_t *q)
{
	/*
	Buffer is not allocated within queue library. Do nothing.
	This is a placeholder function.
	*/
}

int enqueue(queue_t *q, void *data)
{
	int ret = 0;

	if (!q || !data)
		return -1; // Invalid input

	enter_critical_section();
	ret = (q->num_item != 0 && q->wr == q->rd) ? -2 : 0; // Is FIFO Full?
	//if(q->num_item != 0 && q->wr == q->rd) return -2;  //if full? stop enqueue

	memcpy((void *)q->wr, data, q->item_size);
	q->wr = q->wr + q->item_size;
	if (q->wr >= (q->base + q->buffer_size))
		q->wr = q->base;

	q->num_item++;
	if (q->num_item > q->fifo_capacity) {
		q->num_item = q->fifo_capacity;
		q->ovf_item++;
		q->rd = q->wr;
	}

	exit_critical_section();
	return ret;
}

void queue_reset_ovf(queue_t *q)
{
	if (!q)
		return;

	enter_critical_section();
	q->ovf_item = 0;
	exit_critical_section();
}

uint32_t queue_ovf_item(queue_t *q)
{
	if (!q)
		return -1;

	uint32_t ret;
	enter_critical_section();
	ret = q->ovf_item;
	exit_critical_section();
	return ret;
}

int dequeue(queue_t *q, void *data)
{
	if (!q || !data)
		return -1;

	enter_critical_section();
	if (q->num_item <= 0) {
		exit_critical_section();
		return -2;
	}

	memcpy(data, (void *)q->rd, q->item_size);
	q->num_item--;
	q->rd = q->rd + q->item_size;
	if (q->rd >= (q->base + q->buffer_size))
		q->rd = q->base;

#ifdef QUEUE_USAGE_STATS
	q->pop_cnt++;
	if ((q->pop_cnt % q->stats_period_cnt) == 0) {
		if (q->name)
			printf_msg("%s:%d (%s) - %d samples lost, avail:%d \n",
				__func__, __LINE__, q->name, q->ovf_item, q->num_item);
		else
			printf_msg("%s:%d - %d samples lost, avail:%d \n",
				__func__, __LINE__, q->ovf_item, q->num_item);
	}
#endif
	exit_critical_section();
	queue_debug(q);
	return 0;
}

bool queue_is_full(queue_t *q)
{
	if (!q)
		return -1;

	enter_critical_section();
	bool ret = q->wr == q->rd && q->num_item;
	exit_critical_section();
	return ret;
}


int queue_usage(queue_t *q, uint32_t *total, uint32_t *nm_item)
{
	if (!q)
		return -1;

	enter_critical_section();
	*total = q->buffer_size / q->item_size;
	*nm_item = q->num_item;
	exit_critical_section();
	return 0;
}

int queue_pop(queue_t *q)
{
	if (!q)
		return -1;

	enter_critical_section();
	if (q->num_item <= 0) {
		exit_critical_section();
		return -2;
	}

	q->num_item--;
	q->rd = q->rd + q->item_size;
	if (q->rd >= (q->base + q->buffer_size))
		q->rd = q->base;
	exit_critical_section();
#ifdef QUEUE_USAGE_STATS
	q->push_cnt++;
	if ((q->push_cnt % q->stats_period_cnt) == 0) {
		queue_debug(q);
	}
#endif
	return 0;
}

int queue_pop_n(queue_t *q, uint32_t n)
{
	if (!q || n < 1 || n > q->fifo_capacity)
		return -1;

	queue_debug(q);
	enter_critical_section();
	if (q->num_item < n) {
		exit_critical_section();
		return -2;
	}
	uint32_t curr_rd_item_off = (uint32_t)(q->rd - q->base) / q->item_size;
	uint32_t addr_off = (curr_rd_item_off + n) % (q->buffer_size / q->item_size);
	addr_off *= q->item_size;
	q->rd = q->base + addr_off;
	q->num_item -= n;
	exit_critical_section();
	return 0;
}

int queue_front(queue_t *q, void *data)
{
	if (!q || !data)
		return -1;

	enter_critical_section();
	if (q->num_item <= 0) {
		exit_critical_section();
		return -2;
	}

	memcpy(data, (void *)q->rd, q->item_size);
	exit_critical_section();
	return 0;
}

int queue_front_n(queue_t *q, void *data, uint32_t n, uint32_t buf_sz)
{
	if (!q || !data || n < 1)
		return -1;

	enter_critical_section();
	if (n > q->fifo_capacity || buf_sz < (n * q->item_size)) {
		exit_critical_section();
		return -1;
	}

	if (q->num_item < n) {
		exit_critical_section();
		return -2;
	}


	char *dest = (char *)data;
	char *rd = q->rd;
	uint32_t rd_sz = q->item_size * n;
	uint32_t to_end = (uint32_t)(q->base + q->buffer_size - q->rd);
	if (to_end < rd_sz) {
		memcpy(dest, rd, to_end);
		rd_sz -= to_end;
		rd = q->base;
		dest += to_end;
		memcpy(dest, rd, rd_sz);
   } else {
	   memcpy(dest, rd, rd_sz);
   }
#if 0
	char *dest = (char *)data;
	size_t rd_sz = q->item_size * n;
	if ((q->rd + rd_sz) > (q->base + q->buffer_size)) {
		size_t to_end = q->buffer_size - rd_sz;
		memcpy(dest, q->rd, to_end);
		dest += to_end;
		memcpy(dest, q->base, (size_t)(q->rd - q->base));
	} else
		memcpy(dest, q->rd, rd_sz);
#endif
	exit_critical_section();
	queue_debug(q);
	return 0;
}

int enqueue_string(queue_t *q, char *data, uint32_t sz)
{
	int ret = 0;
	int buf_index;
	char *wr_ptr;

	if (!q || !data || sz <= 0)
		return -2; // Invalid parameters

	enter_critical_section();
	ret = (q->wr == q->rd && q->num_item != 0) ? -2 : 0; // Is FIFO Full or Empty?
	if (q->wr >= (q->base + q->buffer_size))
		q->wr = q->base;

	if ((q->num_item + sz) > q->buffer_size) {
		exit_critical_section();
#if defined(QUEUE_DEBUG)
		{
			char buf[128];
			int len;

			if (q->name)
				len = sprintf(buf, "\r\n**** %s - ( %s ) - Fifo is full. num_item: %d, sz: %d, buffer size: %d\r\n",
						__func__, q->name, q->num_item, sz, q->buffer_size);
			else
				len = sprintf(buf, "\r\n**** %s - Fifo is full. num_item: %d, sz: %d, buffer size: %d\r\n",
						__func__, q->num_item, sz, q->buffer_size);

			UART_Write(UART_PORT, (uint8_t*)buf, len);
		}
#endif
		return -3;
	}

	buf_index = (uint32_t)(q->wr - q->base);
	wr_ptr = q->base;
	q->num_item += sz;
	while(sz--)
		wr_ptr[buf_index++ % q->buffer_size] = *data++;

	q->wr = q->base + buf_index % q->buffer_size;
	exit_critical_section();
	return ret;
}

int dequeue_string(queue_t *q, char *buf, uint32_t buffer_size)
{
	char *rd_ptr;
	int buf_index;
	int len;

	if (!q || !buf || buffer_size <= 0)
		return -2;

	enter_critical_section();
	if (q->num_item <= 0) {
		exit_critical_section();
		return -4;
	}

	rd_ptr = (char *)q->base;
	buf_index = (uint32_t)(q->rd - q->base);
	len = q->num_item;

	while (buffer_size-- && q->num_item--) {
		char tmp = rd_ptr[buf_index % q->buffer_size];
		rd_ptr[buf_index % q->buffer_size] = 0; // Remove this later on
		buf_index++;
		*buf++ = tmp;
		//if (tmp == '\0')
                if (tmp == '\0' || q->num_item==0) 
			break;
	}
/*
	if (q->num_item < 0) {
		q->num_item = 0; 
	} else
		len -= q->num_item;
*/
	q->rd = q->base + buf_index % q->buffer_size;
	exit_critical_section();
	return len;
}

int queue_str_len(queue_t *q)
{
	char *rd_ptr;
	int buf_index;
	int len, i;

	if (!q)
		return -2;

	enter_critical_section();
	if (q->num_item <= 0) {
		exit_critical_section();
		return 0;
	}

	rd_ptr = q->base;
	buf_index = (uint32_t)(q->rd - q->base);
	i = q->num_item;
	len = 0;

	while (i--) {
		char tmp = rd_ptr[buf_index % q->buffer_size];
		buf_index++;
		if (tmp == '\0')
			break;
		len++;
	}

	exit_critical_section();
	return len;
}

void queue_n_test(void)
{
	int i,error;
	int16_t acc_data[3];
	int16_t acc_buff[3*10] = {0};
	queue_t acc_queue;
	error = queue_init(&acc_queue, acc_buff, sizeof(acc_data), sizeof(acc_buff));
	if (error){
		printf_msg("queue_init error :(\r\n");
		return;
	}
	int n = 0, ret;
	for(n=0;n<20;n++)
	{
		acc_data[0] = 1098+n;
		acc_data[1] = -2876;
		acc_data[2] = 297+n;
		ret = enqueue(&acc_queue, &acc_data);
		printf_msg("enqueue, ret=%d\n",ret);
	}

	int16_t peek_buf[40];

	error = queue_front_n(&acc_queue, &peek_buf, 5, sizeof(peek_buf));
	if (error) {
		printf_msg("queue_front_n n=5 error :(\r\n");
	} else {
		printf_msg("queue_front_n n=5:\r\n");
		for (i = 0; i < 5; i++) {
			printf_msg("%d %d %d\n", peek_buf[i*3+0],peek_buf[i*3+1],peek_buf[i*3+2]);
		}
	}

	error = queue_front_n(&acc_queue, &peek_buf, 6, sizeof(peek_buf));
	if (error){
		printf_msg("queue_front_n n=6 error :)\r\n");
	}
	else
		printf_msg("queue_front_n n=6 succeeded :(\r\n");


	error = queue_pop_n(&acc_queue, 2);
	if (error){
		printf_msg("queue_pop_n n=2 error :(\r\n");
	}
	else
		printf_msg("queue_pop_n n=2 succeeded :)\r\n");

	error = queue_front_n(&acc_queue, &peek_buf, 5, sizeof(peek_buf));
	if (error){
		printf_msg("queue_front_n n=5 error :)\r\n");
	}
	else{
		printf_msg("queue_pop_n n=5 succeeded :)\r\n");
	}

	error = queue_front_n(&acc_queue, &peek_buf, 3, sizeof(peek_buf));
	if (error) {
		printf_msg("queue_front_n n=3 error :(\r\n");
	} else {
		printf_msg("queue_front_n n=3: ");
		for (i = 0; i < 3; i++) {
			printf_msg("%d %d %d\n", peek_buf[i*3+0],peek_buf[i*3+1], peek_buf[i*3+2]);
		}
	}

	acc_data[0] = 1245;
	acc_data[1] = 3472;
	acc_data[2] = 58;
	enqueue(&acc_queue, &acc_data);
	acc_data[0] = 30671;
	acc_data[1] = -32;
	acc_data[2] = 158;
	enqueue(&acc_queue, &acc_data);

	printf_msg("enqueued 0,1\r\n");

	error = queue_front_n(&acc_queue, &peek_buf, 5, sizeof(peek_buf));
	if (error) {
		printf_msg("queue_front_n n=5 error :(\r\n");
	} else {
		printf_msg("queue_front_n n=5: ");
		for (i = 0; i < 5; i++) {
			printf_msg("%d %d %d\n", peek_buf[i*3+0],peek_buf[i*3+1],peek_buf[i*3+2]);
		}
		printf_msg("\r\n");
	}

	error = queue_pop_n(&acc_queue, 4);
	if (error){
		printf_msg("queue_pop_n n=4 error :(\r\n");
	}
	else
		printf_msg("queue_pop_n n=4 succeeded :)\r\n");

	error = queue_front_n(&acc_queue, &peek_buf, 1, sizeof(peek_buf));
	if (error) {
		printf_msg("queue_front_n n=1 error :(\r\n");
	} else {
		printf_msg("queue_front_n n=1: ");
		for (i = 0; i < 1; i++) {
			printf_msg("%d %d %d\n", peek_buf[i*3+0],peek_buf[i*3+1],peek_buf[i*3+2]);
		}
		printf_msg("\r\n");
	}
}
