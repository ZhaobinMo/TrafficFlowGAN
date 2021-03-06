import torch
import numpy as np
import os
import src.utils as utils

import matplotlib.pyplot as plt
import seaborn as sns
import logging
import os
from src.utils import save_dict_to_json, check_exist_and_create, check_and_make_dir
from torch.utils.tensorboard import SummaryWriter
from src.dataset.gan_helper import  gan_helper

import time

from tqdm import tqdm
from scipy.interpolate import griddata
from tqdm import tqdm

def joint_training(model_1,model_2, optimizer, discriminator, train_feature_1, train_target_1, train_target_2, train_feature_phy, device,
             physics=None,
             physics_optimizer=None,
             restore_from=None,
             epochs=1000,
             batch_size=None,
             experiment_dir=None,
             save_frequency=1,
             verbose_frequency=1,
             verbose_computation_time=0,
             save_each_epoch="False",
             training_gan = False,
             ):

    # Initialize tf.Saver instances to save weights during metrics_factory
    X_train_1 = train_feature_1 #(x,t) for the condition of the first flow
    #X_train_2 = train_feature_2 #(rho) for the condition of the second flow
    y_train_1 = train_target_1 #(rho,z_3) for the first flow
    y_train_2 = train_target_2 #(u,z_3) for the second flow

    X_train_phy = train_feature_phy #(x,t) for physics loss
    begin_at_epoch = 0
    writer = SummaryWriter(os.path.join(experiment_dir, "summary"))
    weights_path = os.path.join(experiment_dir, "weights")
    check_exist_and_create(weights_path)

    if restore_from is not None:
        assert os.path.isfile(restore_from), "restore_from is not a file"
        # restore model
        begin_at_epoch = utils.load_checkpoint(restore_from, model, optimizer, begin_at_epoch)
        logging.info(f"Restoring parameters from {restore_from}, restored epoch is {begin_at_epoch:d}")
    begin_at_epoch = 0

    best_loss = 10000
    best_last_train_loss = {"best":
                                {"loss": 100,
                                 "epoch": 0},
                            "last":
                                {"loss": 100,
                                 "epoch": 0},
                            }
    Data_loss = []
    np.random.seed(1)

    for epoch in tqdm(range(begin_at_epoch, epochs)):
        # shuffle the data
        idx = np.random.choice(X_train_1.shape[0], X_train_1.shape[0], replace=False)
        X_train_1 = X_train_1[idx, :]
        y_train_1 = y_train_1[idx, :]
        #idx = np.random.choice(X_train_1.shape[0], X_train_1.shape[0], replace=False)
        #X_train_2 = X_train_2[idx, :]
        y_train_2 = y_train_2[idx, :]

        idx_phy = np.random.choice(X_train_phy.shape[0], X_train_phy.shape[0], replace=False)
        X_train_phy = X_train_phy[idx_phy,:]


        #### hard code
        # y_train[:,0] = y_train[:,1]
        ####

        # train step
        # num_steps = X_train.shape[0] // batch_size

        num_steps = 100  #! HARDCODE HERE!#

        # batch_size_phy = X_train_phy.shape[0] // num_steps
        batch_size_phy = batch_size
        for step in range(num_steps):
            # x_batch = X_train[step * batch_size:(step + 1) * batch_size, :]
            # y_batch = y_train[step * batch_size:(step + 1) * batch_size, :]
            random_idx = np.random.choice(X_train_1.shape[0], batch_size, replace = False)
            x_batch_1 = X_train_1[random_idx, :]
            y_batch_1 = y_train_1[random_idx, :]
            #random_idx = np.random.choice(X_train_2.shape[0], batch_size, replace = False)
            #x_batch_2 = X_train_2[random_idx, :]
            y_batch_2 = y_train_2[random_idx, :] #(500,2)

            x_batch_2, _ = model_1.test(torch.from_numpy(x_batch_1)) #the condition of the 2nd flow is the output(rho) of the 1st flow
            #(500,1)

            # random sample X_train_phy
            random_idx = np.random.choice(X_train_phy.shape[0], batch_size_phy, replace=False)
            x_batch_phy = X_train_phy[random_idx, :]
            start_time = time.time()
            loss_1, activation_1 = model_1.log_prob(y_batch_1, x_batch_1)
            loss_1 = -loss_1.mean()

            loss_2, activation_2 = model_2.log_prob(y_batch_2, x_batch_2)
            loss_2 = -loss_2.mean()

            loss=loss_1+loss_2

            data_loss = loss_1 + loss_2
            data_loss_np = data_loss.cpu().detach().numpy()
            loss_data_time = time.time()-start_time
            optimizer.zero_grad()

            if physics is not None:
                physics_optimizer.zero_grad()

            # get physics_loss
            if physics is not None:
                start_time = time.time()
                phy_loss, physics_params, grad_hist = physics.get_residuals(model_1, x_batch_phy)
                phy_loss = phy_loss.mean()
                loss_phy_time = time.time() - start_time
                # print(physics_params["tau"])
                loss = loss * physics.hypers["alpha"]
                loss += (1 - physics.hypers["alpha"]) * phy_loss
                phy_loss_np = phy_loss.cpu().detach().numpy()

            if training_gan is True:
                # train the discriminator
                for _ in range(1):
                    if epoch%3 == 0:
                        discriminator.optimizer.zero_grad()
                        Gan_helper = gan_helper(0.02)
                        Ground_truth_figure = Gan_helper.load_ground_truth()
                        rho_test, u_test = model.test(torch.from_numpy(Gan_helper.X_T_low_d).to(device))
                        generator_figure = Gan_helper.reshape_to_figure(rho_test.detach(),u_test.detach())
                        T_real = discriminator.forward(torch.from_numpy(Ground_truth_figure).to(device))
                        T_fake = discriminator.forward(generator_figure)
                        loss_d = - (torch.log(1-torch.sigmoid(T_real)+1e-8) + torch.log(torch.sigmoid(T_fake)+1e-8))
                        loss_d.backward(retain_graph=True)
                        loss_d_np = loss_d.cpu().detach().numpy()
                        discriminator.optimizer.step()
                        del([loss_d, T_real, T_fake, rho_test, u_test, generator_figure])

                # Loss of the generator
                for _ in range(1):
                    optimizer.zero_grad()
                    rho_test, u_test = model.test(torch.from_numpy(Gan_helper.X_T_low_d).to(device))
                    generator_figure = Gan_helper.reshape_to_figure(rho_test, u_test)
                    generator_figure_np = generator_figure.cpu().detach().numpy()
                    T_fake = discriminator.forward(generator_figure)
                    T_real = discriminator.forward(torch.from_numpy(Ground_truth_figure).to(device))
                    loss_g = T_fake
                    loss_g_mse = torch.square(torch.from_numpy(Ground_truth_figure[:,:,:,[0,8,12,24]]).to(device)-
                                              generator_figure[:,:,:,[0,8,12,24]]).mean()
                    T_real_np = T_real.cpu().detach().numpy()
                    loss_g_np = loss_g.cpu().detach().numpy()
                    loss += loss_g_mse  + loss_g.squeeze().squeeze()
                    # loss += 10*loss_g.squeeze().squeeze()


                    loss.backward(retain_graph=True)
                    optimizer.step()

            start_time = time.time()

            loss.backward(retain_graph=True)
            backward_all_time = time.time() - start_time

            start_time = time.time()
            if model_1.train is True:
                optimizer.step()
            step_data_time = time.time() - start_time

            start_time = time.time()
            if physics is not None:
                if physics.train is True:
                    physics_optimizer.step()
                    #pass
            step_phy_time = time.time() - start_time

            if verbose_computation_time == 1:
                print(f"step = {epoch*num_steps + step:d}")
                print(f"loss_data_time: {loss_data_time:.5f}")
                print(f"loss_phys_time: {loss_phy_time:.5f}")
                print(f"backward_all_time: {backward_all_time:.5f}")
                print(f"step_data_time: {step_data_time:.5f}")
                print(f"step_phy_time: {step_phy_time:.5f}")
            # evaluation
            activation_eval_1 = model_1.eval(x_batch_1)
            activation_eval_2 = model_2.eval(x_batch_2)

            # save the data loss
            Data_loss.append(data_loss.cpu().detach().numpy())

            # delete the output tensor
            del([data_loss, loss])
            if physics is not None:
                del(phy_loss)

            # below is for debug
            # a = activation_eval["x1_eval"].detach().cpu().numpy()
            # idx = np.argsort(a)[-1]
            # 4947
            # a = 1

            # below is to force the iteration # for each epoch to be 1.
            # break


        # logging
        if verbose_frequency > 0:
            if epoch % verbose_frequency == 0:
                logging.info(f"Epoch {epoch + 1}/{epochs}    loss={Data_loss[-1]:.3f}")

        # saving at every "save_frequency" or at the last epoch
        if (epoch % save_frequency == 0) | (epoch == begin_at_epoch + epochs - 1):
            is_best = Data_loss[-1] < best_loss
            utils.save_checkpoint({'epoch': epoch + 1,
                                   'state_dict_1': model_1.state_dict(),
                                   'state_dict_2': model_2.state_dict(),
                                   'optim_dict': optimizer.state_dict()},
                                  is_best=is_best,
                                  checkpoint=weights_path,
                                  save_each_epoch=save_each_epoch)

            # if best loss, update the "best_last_train_loss"
            if is_best:
                best_loss = Data_loss[-1]
                best_last_train_loss["best"]["loss"] = best_loss
                best_last_train_loss["best"]["epoch"] = epoch+1

            # update and save the latest "best_last_train_loss"
            best_last_train_loss["last"]["loss"] = Data_loss[-1]
            best_last_train_loss["last"]["epoch"] = epoch+1

            save_path = os.path.join(experiment_dir, "best_last_train_loss.json")
            save_dict_to_json(best_last_train_loss, save_path)

            # save loss to tensorboard
            writer.add_scalar("loss/train", Data_loss[-1], epoch+1)
            writer.add_scalar("loss/train_data_loss", data_loss_np, epoch+1)
            if physics is not None:
                writer.add_scalar("loss/train_phy_loss", phy_loss_np, epoch+1)

            # save activation to tensorboard
            for k, v in activation_1.items():
                writer.add_histogram(f"activation_1_train/{k:s}", v, epoch+1)
            for k, v in activation_2.items():
                writer.add_histogram(f"activation_2_train/{k:s}", v, epoch+1)
            for k, v in activation_eval_1.items():
                writer.add_histogram(f"activation_eval_1/{k:s}", v, epoch+1)
            for k, v in activation_eval_2.items():
                writer.add_histogram(f"activation_eval_2/{k:s}", v, epoch+1)
            if physics is not None:
                # write the physics_params
                for k, v in physics_params.items():
                    if k=='tau':
                        v = v/50.0
                    if k=='nu':
                        v = v ##ground truth ==1
                    writer.add_scalar(f"physics_params/{k:s}", v.mean(), epoch+1)


                # write the hist of the gradient w.r.t x and t
                for k, v in grad_hist.items():
                    writer.add_histogram(f"grad/{k:s}", v, epoch+1)

                for k, v in physics.torch_meta_params.items():
                    if physics.meta_params_trainable[k] == "True":
                        writer.add_scalar(f"physics_grad/dLoss_d{k:s}", v.grad, epoch + 1)


    # plot the abnormal training data loss
    if np.sum( np.where(np.array(Data_loss) > 0) ) > 10*num_steps:
        plt.plot(np.arange(len(Data_loss)), Data_loss)
        plt.xlabel("epoch")
        plt.ylabel("train loss")
        plt.savefig(os.path.join(experiment_dir,"abnormal_train_data_loss.png"))



def test(model, test_feature, test_target,
                restore_from=None,
                metric_functions = None,
                n_samples = None,
                noise = 0.2,
                args=None):
    # experiment_dir: where the model json file locates
    # Initialize tf.Saver instances to save weights during metrics_factory


    if restore_from is not None:
        assert os.path.isfile(restore_from), "restore_from is not a file"
        # restore model
        begin_at_epoch=0
        begin_at_epoch = utils.load_checkpoint(restore_from, model,optimizer=None,epoch= begin_at_epoch,
                                               device=model.device)
        logging.info(f"Restoring parameters from {restore_from}, restored epoch is {begin_at_epoch:d}")

    # make prediction
    #pre_train = not train_PUNN
    #test_feature, test_target = data_test #need to load test data...
    ###
    #test_prediction = model.predict(test_feature, n_repeat=n_samples, pre_train=pre_train)

    samples_mean_rho = np.zeros(( test_feature.shape[0], n_samples )) #115200,n_samples
    samples_mean_u = np.zeros(( test_feature.shape[0], n_samples ))
    torch.manual_seed(1)
    np.random.seed(1)
    for i in tqdm(range(0, n_samples )):
      rho_tensor, u_tensor = model.test(torch.from_numpy(test_feature))
      samples_mean_rho[:,i:i+1],samples_mean_u[:,i:i+1] = rho_tensor.detach().numpy(), u_tensor.detach().numpy()
    rho_star=test_target[:,0][:,None]
    u_star=test_target[:,1][:,None]
    exact_sample_rho = rho_star
    exact_sample_u = u_star
    b = exact_sample_rho
    c = exact_sample_u
    
    for i in range(n_samples -1):
        bb = b + noise * np.random.randn(rho_star.shape[0],1)
        exact_sample_rho = np.hstack((exact_sample_rho, bb))
        cc = c + noise * np.random.randn(u_star.shape[0],1)
        exact_sample_u = np.hstack((exact_sample_u, cc))
    # Compare mean and variance of the predicted samples as prediction and uncertainty
    RHO_pred = np.mean(samples_mean_rho, axis = 1) ##115200,1
    U_pred = np.mean(samples_mean_u, axis = 1) ##115200,1
    test_prediction=np.concatenate([RHO_pred[:,None],U_pred[:,None]],1) ## 115200,2
    #test_target #115200,2
    """
    RHO_pred = griddata( test_feature, RHO_pred.flatten(), (X, T), method='cubic')
    U_pred = griddata(test_feature, U_pred.flatten(), (X, T), method='cubic')
    """


    ###
    # exact_sample_u #115200,n_samples
    # exact_sample_rho #115200,n_samples
    # samples_mean_u #115200,n_samples
    # samples_mean_rho #115200,n_samples
    ###


    # convert to numpy
    #test_target = test_target.numpy() #115200,2
    #test_prediction = test_prediction.numpy() #115200,2

    metrics_dict = dict()
    kl = None
    for k, func in metric_functions.items():
        if k == 'nlpd':
            use_mean = True if args.nlpd_use_mean == "True" else False
            metrics_dict[k] = [func(test_target, test_prediction,
                                   use_mean = use_mean,
                                   n_bands = args.nlpd_n_bands)]            
        elif k == "kl":
            
            ###BCE with logit:
            #kl = func(torch.from_numpy(test_target), torch.from_numpy(test_prediction)).item()
            #metrics_dict[k] =[kl]
            
            
            ###get_KL:
            kl_rho=func(exact_sample_rho, samples_mean_rho)
            key_rho=k+'_rho'
            metrics_dict[key_rho] = [np.mean(kl_rho)]
            kl_u = func(exact_sample_u, samples_mean_u)
            key_u=k+'_u'
            metrics_dict[key_u] = [np.mean(kl_u)]

        elif k == "nll":
            log_prob, _ = model.log_prob(test_target, test_feature)
            nll = -log_prob.mean()
            metrics_dict[k] = [nll]
        else:
           metrics_dict[k] = [func(torch.from_numpy(test_target), torch.from_numpy(test_prediction)).item()]
        print('{}: done'.format(k))

    return metrics_dict, test_prediction, exact_sample_u,exact_sample_rho, samples_mean_u,samples_mean_rho
    



#test(model,test_feature,test_label,   restore_from=restore_from,metric_functions=metric_fns,n_samples=args.test_sample,noise=args.noise,params=params)

def test_multiple_rounds(model, test_feature,test_label, test_rounds=1,
                         save_dir = None,
                         model_alias = None,                        
                **kwargs):
    metrics_dict, test_prediction, exact_sample_u,exact_sample_rho, samples_mean_u,samples_mean_rho = test(model, test_feature,test_label,
                                         **kwargs)
    logging.info("Restoring parameters from {}".format(kwargs["restore_from"]))
    if test_rounds > 1:
        for i in range(test_rounds-1):
            metrics_dict_new= test(model, test_feature,test_label, 
                                         **kwargs) [0]
            for k in metrics_dict.keys():
                metrics_dict[k] += metrics_dict_new[k]
    
    check_and_make_dir(os.path.join(save_dir, model_alias))
    save_path_metric = os.path.join(save_dir, model_alias,
                                    f"metrics_test.json")
    #save_path_prediction = os.path.join(save_dir, model_alias,f"predictions_test.csv")
    #save_path_feature = os.path.join(save_dir, model_alias,f"features_test.csv")
    #save_path_target = os.path.join(save_dir, model_alias,f"targets_test.csv")
    #save_path_kl = os.path.join(save_dir, model_alias,f"kl_test.csv")

    save_path_prediction_rho = os.path.join(save_dir, model_alias,
                                        f"predictions_test_rho.csv")
    save_path_prediction_u = os.path.join(save_dir, model_alias,
                                        f"predictions_test_u.csv")
    save_path_feature = os.path.join(save_dir, model_alias,
                                        f"features_test.csv")
    save_path_target_rho = os.path.join(save_dir, model_alias,
                                        f"targets_test_rho.csv")
    save_path_target_u = os.path.join(save_dir, model_alias,
                                        f"targets_test_u.csv")
    # save_path_kl_rho = os.path.join(save_dir, model_alias,f"kl_rho_test.csv")
    # save_path_kl_u = os.path.join(save_dir, model_alias,f"kl_u_test.csv")
    
    save_dict_to_json(metrics_dict, save_path_metric)
    #np.savetxt(save_path_prediction, test_prediction, delimiter=",")
    #np.savetxt(save_path_feature, test_feature, delimiter=",")
    #np.savetxt(save_path_target, test_label, delimiter=",")
    #np.savetxt(save_path_kl, kl, delimiter=",")
    np.savetxt(save_path_prediction_rho, samples_mean_rho , delimiter=",")
    np.savetxt(save_path_prediction_u, samples_mean_u, delimiter=",")
    np.savetxt(save_path_feature, test_feature, delimiter=",")
    np.savetxt(save_path_target_rho, exact_sample_rho, delimiter=",")
    np.savetxt(save_path_target_u, exact_sample_u, delimiter=",")
    # np.savetxt(save_path_kl_rho, kl_rho, delimiter=",")
    # np.savetxt(save_path_kl_u, kl_u, delimiter=",")



