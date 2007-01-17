#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>

#include <unistd.h>
#include <util.h>
#include <hash.h>
#include <lsf_jobs.h>
#include <ext_job.h>



enum lsf_status_enum_def {lsf_status_null , lsf_status_submitted , lsf_status_running , lsf_status_done , lsf_status_OK , lsf_status_exit};
#define STATUS_SIZE 6

struct lsf_job_struct {
  char             *id;
  char             *run_path;
  char 	  	   *submit_cmd;
  char 	  	   *complete_file;
  time_t  	    submit_time;
  time_t  	    start_time;
  time_t  	    complete_time;
  double  	    run_time_sec;
  int               lsf_id;
  int               max_resubmit;
  int               submit_count;
  lsf_status_enum   status;
};


struct lsf_pool_struct {
  int  		  size;
  int  		  alloc_size;
  int             sleep_time;
  int             max_running;
  
  int            *total_status;
  
  char            *summary_file;
  char            *tmp_path;
  char 		  *tmp_file;
  char 		  *bsub_status_cmd;
  lsf_job_type   **jobList;
  hash_type       *jobs;
  hash_type       *status_tr;
};

/*****************************************************************/

static char * alloc_string_copy(const char *src ) {
  const bool abort_on_NULL = true;
  if (src != NULL) {
    char *copy = calloc(strlen(src) + 1 , sizeof *copy);
    strcpy(copy , src);
    return copy;
  } else {
    if (!abort_on_NULL)
      return NULL;
    else {
      fprintf(stderr,"%s: can not take NULL as input - aborting \n" , __func__);
      abort();
    }
  }

}




/*****************************************************************/

static void lsf_job_set_status(lsf_job_type *lsf_job , lsf_status_enum status) {
  lsf_job->status = status;
}

static lsf_status_enum lsf_job_get_status(const lsf_job_type *lsf_job) {
  return lsf_job->status;
}



lsf_job_type * lsf_job_alloc(const char *id , const char *run_path ,  const char *complete_file, const char *tmp_path, int max_resubmit) {
  const char *submit_cmd = "@eclipse < eclipse.in  2> /dev/null | grep \"Job <\" | cut -f2 -d\"<\" | cut -f1 -d\">\" > ";
  lsf_job_type *lsf_job = malloc(sizeof *lsf_job);
  
  if (id == NULL)
    lsf_job->id = alloc_string_copy(run_path);
  else
    lsf_job->id = alloc_string_copy(id);
  lsf_job->run_path      = alloc_string_copy(run_path);
  lsf_job->complete_file = alloc_string_copy(complete_file);
  lsf_job->submit_cmd = malloc(strlen(run_path) + 7 + strlen(submit_cmd));
  sprintf(lsf_job->submit_cmd , "cd %s ; %s" , lsf_job->run_path , submit_cmd);
  lsf_job->max_resubmit   = max_resubmit;
  lsf_job->submit_count = 0;
  lsf_job_set_status(lsf_job , lsf_status_null);
  return lsf_job;
}




void lsf_job_free(lsf_job_type *lsf_job) {
  free(lsf_job->submit_cmd);
  free(lsf_job->run_path);
  free(lsf_job->id);
  free(lsf_job);
}
					    

int lsf_job_submit(lsf_job_type *lsf_job , const char *tmp_path) {
  if (!util_file_exists(lsf_job->run_path)) {
    fprintf(stderr,"%s: fatal error when submitting job:%s - run_path:%s does not exist \n",__func__ , lsf_job->id , lsf_job->run_path);
    abort();
  }
  const char *tmp_file = "bsub.tmp";
  char *cmd = malloc(strlen(lsf_job->submit_cmd) + strlen(tmp_path) + strlen(tmp_file) + 3);
  char *tmp_file2 = cmd;
  sprintf(cmd , "%s %s/%s" , lsf_job->submit_cmd , tmp_path , tmp_file);
  system(cmd);
  sprintf(tmp_file2 , "%s/%s" , tmp_path , tmp_file);
  {
    FILE *stream = fopen(tmp_file2 , "r");
    int read1;
    read1 = fscanf(stream , "%d" , &lsf_job->lsf_id);
    fclose(stream);
    if (read1 == 1) 
      /*
	Submit status is handled in the pool object.
      */
      time(&lsf_job->submit_time);
    else {
      fprintf(stderr,"Submitting job:%s failed - could not get LSF id \n",lsf_job->id);
      abort();
    }
  }
  lsf_job->submit_count++;
  return lsf_job->lsf_id;
}

static bool lsf_job_can_reschedule(lsf_job_type *lsf_job) {
  if (lsf_job->submit_count <= lsf_job->max_resubmit) {
    return true;
  } else 
    return false;
}



bool lsf_job_complete_OK(lsf_job_type *lsf_job) {
  if (util_file_exists(lsf_job->complete_file)) {
    struct stat buffer;
    int fildes;
    
    fildes = open(lsf_job->complete_file , O_RDONLY);
    fstat(fildes, &buffer);
    lsf_job->complete_time = buffer.st_mtime;
    close(fildes);

    return true;
  }  else
    return false;
}


/*****************************************************************/


lsf_pool_type * lsf_pool_alloc(int sleep_time , int max_running , const char * summary_file , const char *bsub_status_cmd , const char *tmp_path) {
  const char *tmp_file = "bjobs.jobList";
  lsf_pool_type *lsf_pool = malloc(sizeof *lsf_pool);
  lsf_pool->alloc_size = 100;
  lsf_pool->jobList    = calloc(lsf_pool->alloc_size    , sizeof *lsf_pool->jobList);

  if (summary_file != NULL)
    lsf_pool->summary_file = alloc_string_copy(summary_file);
  else
    lsf_pool->summary_file = NULL;

  lsf_pool->tmp_path = alloc_string_copy(tmp_path);
  lsf_pool->tmp_file = malloc(strlen(tmp_path) + strlen(tmp_file) + 2);
  sprintf(lsf_pool->tmp_file , "%s/%s" , tmp_path , tmp_file);
  
  lsf_pool->bsub_status_cmd = malloc(strlen(bsub_status_cmd) + 4 + strlen(lsf_pool->tmp_file));
  sprintf(lsf_pool->bsub_status_cmd , "%s > %s" , bsub_status_cmd , lsf_pool->tmp_file);
  
  lsf_pool->jobs      = hash_alloc(2*lsf_pool->alloc_size);
  lsf_pool->status_tr = hash_alloc(10);
  hash_insert_int(lsf_pool->status_tr , "PEND"   , lsf_status_submitted);
  hash_insert_int(lsf_pool->status_tr , "RUN"    , lsf_status_running);
  hash_insert_int(lsf_pool->status_tr , "SSUSP"  , lsf_status_running);
  hash_insert_int(lsf_pool->status_tr , "USUSP"  , lsf_status_running);
  hash_insert_int(lsf_pool->status_tr , "PSUSP"  , lsf_status_running);
  hash_insert_int(lsf_pool->status_tr , "EXIT"   , lsf_status_exit);
  hash_insert_int(lsf_pool->status_tr , "DONE"   , lsf_status_done);
  
  lsf_pool->sleep_time   = sleep_time;
  lsf_pool->max_running  = max_running;
  lsf_pool->total_status = calloc(STATUS_SIZE , sizeof *lsf_pool->total_status);
    
  lsf_pool->size = 0;
  return lsf_pool;
}

static lsf_status_enum lsf_pool_iget_status(const lsf_pool_type *lsf_pool , int ijob) {
  return lsf_job_get_status(lsf_pool->jobList[ijob]);
}

static void lsf_pool_iset_status(const lsf_pool_type *lsf_pool , int ijob , lsf_status_enum new_status) {
  const lsf_status_enum old_status = lsf_pool_iget_status(lsf_pool , ijob);
  
  if (old_status != lsf_status_OK && old_status != lsf_status_exit) {
    lsf_job_set_status(lsf_pool->jobList[ijob] , new_status);
    printf("Skifter %d -> %d for:%s \n",old_status , new_status , lsf_pool->jobList[ijob]->id);

    lsf_pool->total_status[old_status]--;
    lsf_pool->total_status[new_status]++;
  }
}


static void lsf_pool_isubmit(lsf_pool_type *lsf_pool , int ijob) {
  if (ijob >= lsf_pool->size) {
    fprintf(stderr,"%s: trying to submit job:%d non-existing job - aborting \n",__func__ , ijob);
    abort();
  }
  {
    char char_id[16];
    int new_id = lsf_job_submit(lsf_pool->jobList[ijob] , lsf_pool->tmp_path);
    sprintf(char_id , "%d" , new_id);
    hash_insert_int(lsf_pool->jobs , char_id , ijob);
  }
  lsf_pool_iset_status(lsf_pool , ijob , lsf_status_submitted);
}




int lsf_pool_get_active(const lsf_pool_type *lsf_pool) {
  return lsf_pool->total_status[lsf_status_submitted] + lsf_pool->total_status[lsf_status_running];
}


void lsf_pool_add_job(lsf_pool_type *lsf_pool , const char *id , const char *run_path , const char *complete_file, int max_resubmit) {
  lsf_job_type *new_job = lsf_job_alloc(id , run_path , complete_file , lsf_pool->tmp_path , max_resubmit);

  if (lsf_pool->size == lsf_pool->alloc_size) {
    lsf_pool->alloc_size *= 2;
    lsf_pool->jobList = realloc(lsf_pool->jobList , lsf_pool->alloc_size * sizeof *lsf_pool->jobList);
  }
  lsf_pool->jobList[lsf_pool->size] = new_job;
  lsf_pool->size++;


  /* 
     Dette er eneste punkt hvor netto i total_status endres.
  */
  lsf_pool->total_status[lsf_status_null]++;
  if (lsf_pool_get_active(lsf_pool) < lsf_pool->max_running) 
    lsf_pool_isubmit(lsf_pool , lsf_pool->size - 1);
}





static void lsf_pool_ireschedule(lsf_pool_type *lsf_pool , int ijob) {
  int old_id = lsf_pool->jobList[ijob]->lsf_id;
  if (lsf_job_can_reschedule(lsf_pool->jobList[ijob])) {
    char old_id_char[16];
    lsf_pool_iset_status(lsf_pool , ijob ,  lsf_status_null);
    sprintf(old_id_char , "%d" , old_id);
    hash_del(lsf_pool->jobs , old_id_char); /* We orphan the job which has completed */
  } else 
    lsf_pool_iset_status(lsf_pool, ijob , lsf_status_exit);
}


static bool lsf_pool_complete_OK(const lsf_pool_type *lsf_pool , int ijob) {
  return lsf_job_complete_OK(lsf_pool->jobList[ijob]);
}


static void lsf_pool_update_status(lsf_pool_type *lsf_pool) {
  system(lsf_pool->bsub_status_cmd);
  if (util_file_exists(lsf_pool->tmp_file)) {
    const char newline = '\n';
    bool cont = true;
    int  jobid_int;
    char jobid[16];
    char user[32];
    char status[16];
    FILE *stream = fopen(lsf_pool->tmp_file , "r");;
    char c;
    int read;

    do {
      c = fgetc(stream);
    } while (c != newline);

    do {
      read = fscanf(stream , "%d %s %s",&jobid_int , user , status);
      if (read == 3) {
	sprintf(jobid,"%d" , jobid_int);
	do {
	  c = fgetc(stream);
	} while (c != newline && c != EOF);
	if (c == EOF) cont = false;
	if (hash_has_key(lsf_pool->jobs , jobid)) {
	  int job_nr = hash_get_int(lsf_pool->jobs , jobid);
	  lsf_pool_iset_status(lsf_pool , job_nr , hash_get_int(lsf_pool->status_tr , status));
	}
      } else if (read == 0) {
	do {
	  c = fgetc(stream);
	} while (c != newline && c != EOF);
	if (c == EOF) cont = false;
      }
      
      if (cont) {
	c = fgetc(stream);
	if (c == EOF) 
	  cont = false;
	else
	  ungetc(c , stream);
      }
    } while (cont);
    fclose(stream);
    unlink(lsf_pool->tmp_file);
    printf("Update1      total:%2d %2d %2d | %2d %2d %2d \n",lsf_pool->total_status[0] , lsf_pool->total_status[1] , lsf_pool->total_status[2] , lsf_pool->total_status[3],
	   lsf_pool->total_status[4] , lsf_pool->total_status[5]);  
    /*{
      int ijob;
      for (ijob = 0; ijob  < STATUS_SIZE; ijob++)
	lsf_pool->total_status[ijob] = 0;
      
      for (ijob = 0; ijob  < lsf_pool->size; ijob++)
	lsf_pool->total_status[lsf_job_get_status(lsf_pool->jobList[ijob])]++;
    }
    */
    /*
      printf("Update2      total:%2d %2d %2d | %2d %2d %2d \n",lsf_pool->total_status[0] , lsf_pool->total_status[1] , lsf_pool->total_status[2] , lsf_pool->total_status[3],
	   lsf_pool->total_status[4] , lsf_pool->total_status[5]);  
    */
  } else {
    fprintf(stderr,"%s: failed to find status file:%s ... aborting \n", __func__ , lsf_pool->tmp_file);
    abort();
  }
}





int lsf_pool_run_jobs(lsf_pool_type *lsf_pool, bool sub_exit) {
  bool cont;
  int ijob;
  do {
    sleep(lsf_pool->sleep_time);
    cont = true;
    printf("Starter      total:%2d %2d %2d | %2d %2d %2d \n",lsf_pool->total_status[0] , lsf_pool->total_status[1] , lsf_pool->total_status[2] , lsf_pool->total_status[3],
	   lsf_pool->total_status[4] , lsf_pool->total_status[5]);  
    /* 
       First step: submitting idle jobs 
    */
    if (lsf_pool_get_active(lsf_pool) < lsf_pool->max_running) {
      ijob = 0;
      do {
	if (lsf_pool_iget_status(lsf_pool , ijob) == lsf_status_null)
	  lsf_pool_isubmit(lsf_pool , ijob);
	ijob++;
      } while (lsf_pool_get_active(lsf_pool) < lsf_pool->max_running && ijob < lsf_pool->size);
    }
    printf("Etter resubm total:%2d %2d %2d | %2d %2d %2d \n",lsf_pool->total_status[0] , lsf_pool->total_status[1] , lsf_pool->total_status[2] , lsf_pool->total_status[3],
	   lsf_pool->total_status[4] , lsf_pool->total_status[5]);  

    /*
      Second step: update status
    */
    lsf_pool_update_status(lsf_pool);
    printf("Etter update total:%2d %2d %2d | %2d %2d %2d \n",lsf_pool->total_status[0] , lsf_pool->total_status[1] , lsf_pool->total_status[2] , lsf_pool->total_status[3],
	   lsf_pool->total_status[4] , lsf_pool->total_status[5]);  
    /*
      Third step: check complete jobs.
    */
    for (ijob = 0; ijob < lsf_pool->size; ijob++) {
      if (lsf_pool_iget_status(lsf_pool , ijob) == lsf_status_done) {
	if (lsf_pool_complete_OK(lsf_pool , ijob)) { 
	  lsf_pool_iset_status(lsf_pool , ijob , lsf_status_OK);
	} else {
	  printf("Could not find result_file %s rescheduling: %s \n",lsf_pool->jobList[ijob]->complete_file , lsf_pool->jobList[ijob]->id);
	  lsf_pool_ireschedule(lsf_pool , ijob);
	}
      }
    }
    
    if (lsf_pool->total_status[lsf_status_OK] + lsf_pool->total_status[lsf_status_exit] == lsf_pool->size)
      cont = false;
    
    if (sub_exit) {
      if (lsf_pool->total_status[lsf_status_null] == 0) 
	cont = false;
    }
    
  } while (cont);
  /*
    Print warning about failed jobs.
  */
  if (lsf_pool->total_status[lsf_status_exit] >= 0) {
    for (ijob = 0; ijob < lsf_pool->size; ijob++) {
      if (lsf_pool_iget_status(lsf_pool , ijob) == lsf_status_exit)
	printf("Job : %03d / %s failed \n",ijob + 1,lsf_pool->jobList[ijob]->id);
    }
  }
      
  return lsf_pool->total_status[lsf_status_exit];
}


void lsf_pool_set_fail_vector(const lsf_pool_type * lsf_pool , int *fail_vector) {
  int ijob;
  int ifail = 0;
  for (ijob = 0; ijob < lsf_pool->size; ijob++) {
    if (lsf_pool_iget_status(lsf_pool , ijob) == lsf_status_exit) {
      fail_vector[ifail] = ijob;
      ijob++;
    }
  }
}



void lsf_pool_free(lsf_pool_type *lsf_pool) {
  free(lsf_pool->tmp_path);
  free(lsf_pool->tmp_file);
  free(lsf_pool->bsub_status_cmd);
  hash_free(lsf_pool->jobs);
  hash_free(lsf_pool->status_tr);
  {
    int i;
    for (i=0; i < lsf_pool->size; i++)
      lsf_job_free(lsf_pool->jobList[i]);
    free(lsf_pool->jobList);
  }
  if (lsf_pool->summary_file != NULL)
    free(lsf_pool->summary_file);
  free(lsf_pool->total_status);
  free(lsf_pool);
}
